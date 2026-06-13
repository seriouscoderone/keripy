#!/usr/bin/env python3
"""Empirical probe: how stale is an immediate GSI read-after-write for keripy's
DynamoDBer access pattern, and how long does it take to converge?

keripy's `DynamoDBer` (src/keri/db/dynamodbing.py) serves all *ordered/range*
reads -- KEL-by-sequence-number, "get the latest event" (`appendOnVal`/getLast
pattern, `_query_gsi(..., forward=False)` then `items[0]`), escrow scans, and
counts -- through a `subdb-index` GSI on (`gsi_pk`, `gsi_sk`). DynamoDB makes
GSIs ALWAYS eventually consistent: there is no `ConsistentRead=True` for an
index. A read issued immediately after the write that should have populated it
can therefore miss the new row or return a stale "latest".

This script quantifies that window against REAL AWS (moto / DynamoDB-Local
report zero staleness because they update indexes synchronously, so the answer
they give is a lie for this question). It:

  1. creates a throwaway table (PK/SK + `subdb-index` GSI, projection ALL,
     PAY_PER_REQUEST) -- same schema as `KeriCoreStack.CoreTable`;
  2. simulates a KEL append for a single namespace ns="probe:kel", subdb "kels",
     writing monotonically increasing sequence numbers 0..N-1, reproducing
     DynamoDBer's real key shapes (see below);
  3. per write, takes two GSI measurements + one strong base-table control read;
  4. runs a bursty pass (~50 back-to-back writes, then one GSI get-latest);
  5. prints a report with miss%, stale%, catch-up latency percentiles, control
     misses, and a one-line VERDICT;
  6. tears the table down and verifies zero `gsi-stale-probe-*` tables remain.

Everything is created with a unique `gsi-stale-probe-<suffix>` name and torn
down at the end (unless --keep). It touches NONE of your existing tables/stacks.
No IAM roles are created -- the caller's own creds issue every call.

Usage:
  python probe.py --region us-east-1                 # create, measure, teardown
  python probe.py --region us-east-1 --keep          # leave the table to inspect
  python probe.py --region us-east-1 --teardown-only --suffix <suffix>  # cleanup
  python probe.py --region us-east-1 --iters 300 --burst 50  # tune the load

Real key shapes reproduced (src/keri/db/dynamodbing.py):
  An ordered append (appendOnVal) stores val at onKey(basekey, on) where
    onKey = basekey + b"." + b"%032x" % on   (32-hex zero-padded ordinal)
  and the DynamoDB item is (_put_item / _gsi_pk / _gsi_sk):
    PK     = "{ns}#{subdb}#{hex(onkey)}"     (namespace#subdb#hex(full key))
    SK     = "V"                              (single-value sort key, _SK_SINGLE)
    gsi_pk = "{ns}#{subdb}"                   (subdb partition in the index)
    gsi_sk = "{hex(onkey)}"                   (hex of the FULL onkey)
  Because the ordinal is fixed-width 32-hex and the base key is constant, the
  hex of the onkey sorts lexically in write order -- exactly how `getLast`
  (ScanIndexForward=False, Limit-ish first item) reads "the latest event".
"""
import argparse
import statistics
import sys
import time

import boto3
from botocore.exceptions import ClientError

GSI_NAME = "subdb-index"
NS = "probe:kel"        # namespace, mirrors Service AID baser pooled as "{alias}:kel"
SUBDB = "kels"          # an ordered subdb (KELs are ordered by sequence number)
SK_SINGLE = "V"         # _SK_SINGLE in dynamodbing.py
SEP = b"."

POLL_INTERVAL_S = 0.005  # 5 ms tight poll
POLL_CAP_S = 2.0         # give up after ~2 s


# ── faithful key shapes (mirror dynamodbing.py) ───────────────────────────────
def _hex(val: bytes) -> str:
    if isinstance(val, str):
        val = val.encode("utf-8")
    return val.hex()


def onkey(basekey: bytes, on: int) -> bytes:
    """onKey(top, on): top + sep + 32-hex-padded ordinal. (dynamodbing.onKey)"""
    return b"%s%s%032x" % (basekey, SEP, on)


def gsi_pk() -> str:
    return f"{NS}#{SUBDB}"


def kel_item(basekey: bytes, on: int) -> dict:
    """One ordered KEL append at sequence number `on`, as DynamoDBer writes it."""
    full = onkey(basekey, on)
    h = _hex(full)
    return {
        "PK": {"S": f"{NS}#{SUBDB}#{h}"},
        "SK": {"S": SK_SINGLE},
        "gsi_pk": {"S": gsi_pk()},
        "gsi_sk": {"S": h},
        "val": {"S": f"event-{on}"},
    }


# ── setup ─────────────────────────────────────────────────────────────────────
def create_table(ddb, table):
    print(f"  creating table {table} ...")
    ddb.create_table(
        TableName=table,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "gsi_pk", "AttributeType": "S"},
            {"AttributeName": "gsi_sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": GSI_NAME,
            "KeySchema": [
                {"AttributeName": "gsi_pk", "KeyType": "HASH"},
                {"AttributeName": "gsi_sk", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )
    ddb.get_waiter("table_exists").wait(TableName=table)
    # GSI also needs to become ACTIVE before queries behave normally.
    for _ in range(60):
        desc = ddb.describe_table(TableName=table)["Table"]
        gsis = desc.get("GlobalSecondaryIndexes", [])
        if gsis and all(g["IndexStatus"] == "ACTIVE" for g in gsis):
            break
        time.sleep(1)
    print("  table + GSI active")


# ── the two GSI measurements + control ────────────────────────────────────────
def gsi_query_exact(ddb, table, gsi_sk_val):
    """GSI Query for the EXACT gsi_sk just written (eq). Returns count present."""
    resp = ddb.query(
        TableName=table, IndexName=GSI_NAME,
        KeyConditionExpression="gsi_pk = :g AND gsi_sk = :s",
        ExpressionAttributeValues={":g": {"S": gsi_pk()}, ":s": {"S": gsi_sk_val}},
    )
    return resp.get("Count", 0)


def gsi_get_latest(ddb, table):
    """The `getLast` pattern: newest-first GSI query, Limit=1. Returns gsi_sk or None."""
    resp = ddb.query(
        TableName=table, IndexName=GSI_NAME,
        KeyConditionExpression="gsi_pk = :g",
        ExpressionAttributeValues={":g": {"S": gsi_pk()}},
        ScanIndexForward=False, Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        return None
    return items[0]["gsi_sk"]["S"]


def base_get_strong(ddb, table, basekey, on):
    """Control: strongly-consistent base-table point read of the row just written."""
    h = _hex(onkey(basekey, on))
    resp = ddb.get_item(
        TableName=table,
        Key={"PK": {"S": f"{NS}#{SUBDB}#{h}"}, "SK": {"S": SK_SINGLE}},
        ConsistentRead=True,
    )
    return "Item" in resp


def poll_until_visible(ddb, table, gsi_sk_val):
    """After a MISS, poll the GSI until the exact item appears. Returns ms to
    converge, or None if it never showed within the cap."""
    start = time.perf_counter()
    while True:
        if gsi_query_exact(ddb, table, gsi_sk_val) > 0:
            return (time.perf_counter() - start) * 1000.0
        if (time.perf_counter() - start) >= POLL_CAP_S:
            return None
        time.sleep(POLL_INTERVAL_S)


# ── the measurement loop ──────────────────────────────────────────────────────
def run_measurements(ddb, table, iters):
    basekey = b"E_probe_aid_prefix_placeholder_kel"  # a stand-in KEL base key (constant)

    exact_miss = 0
    catchup_ms = []          # latency for the MISS cases that later converged
    never_converged = 0      # MISS that didn't show within POLL_CAP_S

    stale_latest = 0
    stale_lag = []           # (i - seen_on) for STALE get-latest cases
    latest_none = 0          # get-latest returned nothing at all (empty index view)

    control_miss = 0

    print(f"  running {iters} iterations (write seq 0..{iters - 1}) ...")
    for i in range(iters):
        item = kel_item(basekey, i)
        gsi_sk_val = item["gsi_sk"]["S"]
        ddb.put_item(TableName=table, Item=item)

        # (1) exact-item visibility through the GSI, immediately.
        if gsi_query_exact(ddb, table, gsi_sk_val) == 0:
            exact_miss += 1
            ms = poll_until_visible(ddb, table, gsi_sk_val)
            if ms is None:
                never_converged += 1
            else:
                catchup_ms.append(ms)

        # (2) "get-latest" correctness (the kels.getLast pattern), immediately.
        latest_sk = gsi_get_latest(ddb, table)
        if latest_sk is None:
            latest_none += 1
            stale_latest += 1
            stale_lag.append(i + 1)  # saw "nothing" while i was just written
        else:
            # Decode the seen ordinal back out of hex(onkey) -> trailing 32 hex.
            seen_full = bytes.fromhex(latest_sk)
            seen_on = int(seen_full.rsplit(SEP, 1)[1], 16)
            if seen_on != i:
                stale_latest += 1
                stale_lag.append(i - seen_on)

        # (control) strongly-consistent base point read must ALWAYS see the write.
        if not base_get_strong(ddb, table, basekey, i):
            control_miss += 1

        if (i + 1) % 50 == 0:
            print(f"    {i + 1}/{iters}  (exact-miss={exact_miss}, stale-latest={stale_latest})")

    return {
        "iters": iters,
        "exact_miss": exact_miss,
        "catchup_ms": catchup_ms,
        "never_converged": never_converged,
        "stale_latest": stale_latest,
        "stale_lag": stale_lag,
        "latest_none": latest_none,
        "control_miss": control_miss,
    }


def run_burst(ddb, table, iters, burst):
    """Bursty pass: write `burst` rows back-to-back with NO delay, then one GSI
    get-latest -- does it see the very last write? Ordinals continue past `iters`
    so they remain monotonically increasing and lexically last."""
    basekey = b"E_probe_aid_prefix_placeholder_kel"
    last_on = iters + burst - 1
    print(f"  bursty pass: {burst} back-to-back writes (seq {iters}..{last_on}) ...")
    for i in range(iters, iters + burst):
        ddb.put_item(TableName=table, Item=kel_item(basekey, i))
    latest_sk = gsi_get_latest(ddb, table)
    if latest_sk is None:
        return {"saw_last": False, "seen_on": None, "expected_on": last_on}
    seen_on = int(bytes.fromhex(latest_sk).rsplit(SEP, 1)[1], 16)
    return {"saw_last": seen_on == last_on, "seen_on": seen_on, "expected_on": last_on}


# ── report ────────────────────────────────────────────────────────────────────
def pct(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def print_report(r, burst):
    n = r["iters"]
    miss_pct = 100.0 * r["exact_miss"] / n if n else 0.0
    stale_pct = 100.0 * r["stale_latest"] / n if n else 0.0
    cm = r["catchup_ms"]

    print("\n" + "=" * 92)
    print("GSI READ-AFTER-WRITE STALENESS PROBE -- RESULTS")
    print(f"  pattern: ns={NS!r} subdb={SUBDB!r}  ({n} ordered KEL appends, GSI '{GSI_NAME}')")
    print("=" * 92)

    print("\n[1] EXACT-ITEM VISIBILITY  (GSI Query gsi_sk == just-written, immediately)")
    print(f"      MISS on immediate GSI read : {r['exact_miss']}/{n}  ({miss_pct:.1f}%)")
    if cm:
        print(f"      catch-up latency (ms)      : "
              f"p50={pct(cm, 50):.1f}  p90={pct(cm, 90):.1f}  "
              f"p99={pct(cm, 99):.1f}  max={max(cm):.1f}  (min={min(cm):.1f})")
    else:
        print("      catch-up latency (ms)      : n/a (no misses, or none converged)")
    if r["never_converged"]:
        print(f"      DID NOT converge within {POLL_CAP_S*1000:.0f} ms : {r['never_converged']}")

    print("\n[2] GET-LATEST CORRECTNESS  (GSI Query newest-first Limit=1 == 'read latest key state')")
    print(f"      STALE (newest seen != just-written) : {r['stale_latest']}/{n}  ({stale_pct:.1f}%)")
    if r["stale_lag"]:
        print(f"      lag when stale (events behind)      : "
              f"p50={pct(r['stale_lag'], 50):.0f}  p90={pct(r['stale_lag'], 90):.0f}  "
              f"max={max(r['stale_lag'])}")
    if r["latest_none"]:
        print(f"      get-latest returned EMPTY index     : {r['latest_none']}  "
              f"(GSI had no rows yet for this partition)")

    print("\n[3] CONTROL  (base-table strong point read of the row just written)")
    flag = "" if r["control_miss"] == 0 else "   <<< ALARMING"
    print(f"      strong point-read MISSES : {r['control_miss']}/{n}  (expect 0){flag}")

    b = r["burst"]
    print(f"\n[4] BURSTY PASS  ({burst} back-to-back writes, then one GSI get-latest)")
    if b["saw_last"]:
        print(f"      saw the last write : YES (seen on={b['seen_on']} == expected {b['expected_on']})")
    else:
        seen = b["seen_on"]
        behind = "EMPTY index" if seen is None else f"{b['expected_on'] - seen} behind"
        print(f"      saw the last write : NO  (seen on={seen}, expected {b['expected_on']} -- {behind})")

    print("\n" + "-" * 92)
    # ── one-line verdict ──
    p99 = pct(cm, 99) if cm else float("nan")
    if r["control_miss"]:
        print(f"VERDICT: !! CONTROL FAILED -- {r['control_miss']} strong base point reads missed; "
              f"creds/region/clock anomaly, treat GSI numbers with suspicion.")
    elif r["exact_miss"] == 0 and r["stale_latest"] == 0:
        print("VERDICT: immediate GSI read-after-write was FRESH on every iteration this run "
              "(0% miss, 0% stale) -- but the GSI is still only EVENTUALLY consistent by "
              "contract; absence of staleness here is luck/low-load, NOT a guarantee.")
    else:
        lat = f"~{p99:.0f} ms p99" if cm else "an unmeasured window"
        print(f"VERDICT: immediate GSI read-after-write is STALE ~{stale_pct:.1f}% of the time "
              f"for 'get-latest' (exact-item MISS {miss_pct:.1f}%), converging within {lat}; "
              f"base-table strong point reads were 100% fresh. Do NOT serve "
              f"read-after-write 'latest key state' off the GSI.")
    print("=" * 92 + "\n")


# ── teardown / leftover verification ──────────────────────────────────────────
def teardown(ddb, table):
    try:
        ddb.delete_table(TableName=table)
        ddb.get_waiter("table_not_exists").wait(TableName=table)
        print(f"  deleted table {table}")
    except ClientError as e:
        print(f"  (table {table}: {e.response['Error']['Code']})")


def list_leftovers(ddb, prefix="gsi-stale-probe-"):
    names, kwargs = [], {}
    while True:
        resp = ddb.list_tables(**kwargs)
        names.extend(n for n in resp.get("TableNames", []) if n.startswith(prefix))
        last = resp.get("LastEvaluatedTableName")
        if not last:
            break
        kwargs["ExclusiveStartTableName"] = last
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--suffix", default="run1", help="table name suffix")
    ap.add_argument("--iters", type=int, default=300, help="ordered-append iterations")
    ap.add_argument("--burst", type=int, default=50, help="bursty-pass write count")
    ap.add_argument("--keep", action="store_true", help="skip teardown")
    ap.add_argument("--teardown-only", action="store_true", help="only tear down (needs --suffix)")
    args = ap.parse_args()

    session = boto3.Session(region_name=args.region)
    ident = session.client("sts").get_caller_identity()
    print(f"account={ident['Account']}  caller={ident['Arn']}  region={args.region}")

    table = f"gsi-stale-probe-{args.suffix}"
    ddb = session.client("dynamodb")

    if args.teardown_only:
        print("teardown-only:")
        teardown(ddb, table)
        left = list_leftovers(ddb)
        print(f"  leftover gsi-stale-probe-* tables: {left if left else 'NONE'}")
        return 0

    print("\n[1/4] create table + GSI"); create_table(ddb, table)
    print("[2/4] measure (exact-visibility + get-latest + control)")
    r = run_measurements(ddb, table, args.iters)
    print("[3/4] bursty pass")
    r["burst"] = run_burst(ddb, table, args.iters, args.burst)
    print("[4/4] report")
    print_report(r, args.burst)

    if args.keep:
        print(f"--keep set: leaving table {table}. Clean up later with:\n"
              f"  python probe.py --region {args.region} --teardown-only --suffix {args.suffix}")
    else:
        print("teardown:")
        teardown(ddb, table)
        left = list_leftovers(ddb)
        if left:
            print(f"  !! leftover gsi-stale-probe-* tables remain: {left}")
            return 2
        print("  verified: ZERO leftover gsi-stale-probe-* tables remain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
