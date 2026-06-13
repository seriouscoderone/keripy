#!/usr/bin/env python3
"""Empirical probe: do `DynamoDBer.appendOnVal` / `addIoSetVal` survive a REAL
concurrent-writer race?

keripy's `DynamoDBer` (src/keri/db/dynamodbing.py) computes the "next ordinal"
for an ordered append (`appendOnVal`) and the "next ion" for an insertion-ordered
set (`addIoSetVal`) by reading the current max through the `subdb-index` GSI —
which is ALWAYS eventually consistent (there is no `ConsistentRead=True` for an
index). Two Lambda instances appending to the SAME key can both read the same
stale max, compute the same next ordinal, and collide.

The fix (this branch): both methods now land via a strongly-consistent
conditional put (`attribute_not_exists(PK)`) and, on a collision, advance the
ordinal/ion LOCALLY and retry — bounded by `_APPEND_MAX_RETRY` (64). So:
  - appendOnVal no longer raises on collision (pre-fix it raised ValueError) and
    never drops/overwrites an append;
  - addIoSetVal no longer silently overwrites a colliding ion (pre-fix it
    computed `max_ion+1` once and put with NO condition, clobbering a peer).

**moto / DynamoDB-Local cannot reproduce this** — their GSI is updated
synchronously, so the stale-read window never opens and every writer sees the
true max. This probe therefore runs N REAL OS processes (multiprocessing), each
with its OWN `DynamoDBer` instance (separate boto3 client = a separate "Lambda
instance"), all hammering the SAME key against a REAL DynamoDB table, and proves
nothing is dropped or overwritten.

It uses the REAL shipped `DynamoDBer` so it exercises the actual methods.

Two storms:
  - appendOnVal storm: N workers x M appends of distinct values to ONE key.
    Assert landed == N*M (zero drops), ordinals unique (zero overwrites),
    ordinals contiguous 0..N*M-1.
  - addIoSetVal storm: N workers x M adds of globally-distinct values to ONE
    IoSet key. Assert all N*M distinct values present (zero lost to overwrite)
    and ions unique.

Everything is created with a unique `concurrent-append-probe-<suffix>` name and
torn down at the end (unless --keep). It touches NONE of your existing
tables/stacks and creates NO IAM roles (the caller's own creds issue every call).

Usage:
  python probe.py --region us-east-1                         # create, storm, teardown
  python probe.py --region us-east-1 --workers 8 --appends 25
  python probe.py --region us-east-1 --keep                  # leave table to inspect
  python probe.py --region us-east-1 --teardown-only --suffix run1
"""
import argparse
import multiprocessing as mp
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

GSI_NAME = "subdb-index"
GSI_PK = "gsi_pk"
GSI_SK = "gsi_sk"

# DynamoDBer instance name == namespace; all workers MUST share it so they
# target the same partition for the same key.
PROBE_NAME = "probe"
STORE = "evts."                  # an ordered store; trailing-dot store name
APPEND_KEY = b"E_concurrent_append_probe_kel"
IOSET_KEY = b"E_concurrent_ioset_probe_set"


# ── worker entrypoints (each runs in its OWN OS process) ─────────────────────
def _append_worker(table_name, region, count, q):
    """One Lambda-like instance: its own DynamoDBer/boto3 client, N appends."""
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    sdb = db.env.open_db(STORE.encode())
    pid = os.getpid()
    ok = err = 0
    last_err = ""
    for i in range(count):
        try:
            db.appendOnVal(sdb, APPEND_KEY, val=f"{pid}-{i}".encode())
            ok += 1
        except Exception as e:  # noqa: BLE001 — a caught append = a drop, count it
            err += 1
            last_err = f"{type(e).__name__}: {e}"
    q.put(("append", pid, ok, err, last_err))


def _ioset_worker(table_name, region, count, q):
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    sdb = db.env.open_db(STORE.encode())
    pid = os.getpid()
    ok = err = 0
    last_err = ""
    for i in range(count):
        try:
            # GLOBALLY-distinct value: every (pid, i) pair is unique, so the
            # set must end up with exactly N*M members if nothing is lost.
            db.addIoSetVal(sdb, IOSET_KEY, val=f"{pid}-{i}".encode())
            ok += 1
        except Exception as e:  # noqa: BLE001
            err += 1
            last_err = f"{type(e).__name__}: {e}"
    q.put(("ioset", pid, ok, err, last_err))


def _run_storm(target, table_name, region, workers, count):
    """Spawn `workers` processes, join, collect (ok, err) + per-worker rows."""
    ctx = mp.get_context("spawn")  # fresh interpreter per worker = fresh client
    q = ctx.Queue()
    procs = [ctx.Process(target=target, args=(table_name, region, count, q))
             for _ in range(workers)]
    t0 = time.perf_counter()
    for p in procs:
        p.start()
    rows = [q.get() for _ in procs]
    for p in procs:
        p.join()
    dt = time.perf_counter() - t0
    total_ok = sum(r[2] for r in rows)
    total_err = sum(r[3] for r in rows)
    return {"rows": rows, "ok": total_ok, "err": total_err, "secs": dt}


# ── read-back enumeration via the REAL DynamoDBer accessors ──────────────────
def _open_db(table_name, region):
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    sdb = db.env.open_db(STORE.encode())
    return db, sdb


def enumerate_append(table_name, region):
    """All ordinals landed under APPEND_KEY, via getOnAllItemIter -> (key,on,val)."""
    db, sdb = _open_db(table_name, region)
    ons, vals = [], []
    for _ckey, on, val in db.getOnAllItemIter(sdb, APPEND_KEY):
        ons.append(on)
        vals.append(bytes(val))
    return ons, vals


def enumerate_ioset(table_name, region):
    """All (ion, val) landed under IOSET_KEY, via the real _get_ioset_raw."""
    db, sdb = _open_db(table_name, region)
    raw = db._get_ioset_raw(sdb, IOSET_KEY)  # [(ion, val), ...] sorted by ion
    ions = [ion for ion, _ in raw]
    vals = [bytes(v) for _, v in raw]
    return ions, vals


# ── table lifecycle ──────────────────────────────────────────────────────────
def create_table(ddb, table):
    print(f"  creating table {table} ...")
    ddb.create_table(
        TableName=table,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": GSI_PK, "AttributeType": "S"},
            {"AttributeName": GSI_SK, "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": GSI_NAME,
            "KeySchema": [
                {"AttributeName": GSI_PK, "KeyType": "HASH"},
                {"AttributeName": GSI_SK, "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )
    ddb.get_waiter("table_exists").wait(TableName=table)
    # GSI must become ACTIVE too — appendOnVal reads its max through the GSI.
    for _ in range(60):
        desc = ddb.describe_table(TableName=table)["Table"]
        gsis = desc.get("GlobalSecondaryIndexes", [])
        if gsis and all(g["IndexStatus"] == "ACTIVE" for g in gsis):
            break
        time.sleep(1)
    print("  table + GSI active")


def teardown(ddb, table):
    try:
        ddb.delete_table(TableName=table)
        ddb.get_waiter("table_not_exists").wait(TableName=table)
        print(f"  deleted table {table}")
    except ClientError as e:
        print(f"  (table {table}: {e.response['Error']['Code']})")


def list_leftovers(ddb, prefix="concurrent-append-probe-"):
    names, kwargs = [], {}
    while True:
        resp = ddb.list_tables(**kwargs)
        names.extend(n for n in resp.get("TableNames", []) if n.startswith(prefix))
        last = resp.get("LastEvaluatedTableName")
        if not last:
            break
        kwargs["ExclusiveStartTableName"] = last
    return names


# ── report ────────────────────────────────────────────────────────────────────
def _storm_findings(label, expected, landed, ordinals, vals, worker_err,
                    *, contiguous_check, distinct_value_check):
    """Returns (rows, passed) where rows is a list of (metric, value) pairs."""
    uniq_ord = len(set(ordinals)) == len(ordinals)
    contiguous = (sorted(ordinals) == list(range(expected))) if contiguous_check else None
    distinct_vals = (len(set(vals)) == expected) if distinct_value_check else None

    passed = (landed == expected) and uniq_ord and (worker_err == 0)
    if contiguous_check:
        passed = passed and bool(contiguous)
    if distinct_value_check:
        passed = passed and bool(distinct_vals)

    rows = [
        ("expected", expected),
        ("landed", f"{landed}   ({'OK' if landed == expected else 'MISMATCH — DROPS/OVERWRITES'})"),
        ("ordinals unique?", "YES" if uniq_ord else f"NO ({len(ordinals) - len(set(ordinals))} dup)"),
    ]
    if contiguous_check:
        rows.append(("contiguous 0..N-1?", "YES" if contiguous else "NO (gap)"))
    if distinct_value_check:
        rows.append(("distinct values present?",
                     "YES" if distinct_vals else f"NO ({len(set(vals))}/{expected})"))
    rows.append(("total worker errors", f"{worker_err}   ({'clean' if worker_err == 0 else 'APPENDS RAISED'})"))
    return rows, passed


def print_report(workers, appends, append_res, append_rb, ioset_res, ioset_rb):
    expected = workers * appends
    a_ons, a_vals = append_rb
    i_ions, i_vals = ioset_rb

    a_rows, a_pass = _storm_findings(
        "appendOnVal", expected, len(a_ons), a_ons, a_vals, append_res["err"],
        contiguous_check=True, distinct_value_check=False)
    i_rows, i_pass = _storm_findings(
        "addIoSetVal", expected, len(i_ions), i_ions, i_vals, ioset_res["err"],
        contiguous_check=True, distinct_value_check=True)

    print("\n" + "=" * 92)
    print("CONCURRENT-APPEND RACE PROBE — RESULTS")
    print(f"  {workers} OS-process workers x {appends} ops each  =  {expected} expected, ONE shared key")
    print("=" * 92)

    print(f"\n[1] appendOnVal storm  (key={APPEND_KEY.decode()!r}, {append_res['secs']:.1f}s)")
    for k, v in a_rows:
        print(f"      {k:<28}: {v}")

    print(f"\n[2] addIoSetVal storm  (key={IOSET_KEY.decode()!r}, {ioset_res['secs']:.1f}s)")
    for k, v in i_rows:
        print(f"      {k:<28}: {v}")

    # Per-worker error detail (only if any storm had errors)
    if append_res["err"] or ioset_res["err"]:
        print("\n  per-worker error detail (pid: ok/err  last_err):")
        for label, res in (("append", append_res), ("ioset", ioset_res)):
            for _kind, pid, ok, err, last in res["rows"]:
                if err:
                    print(f"      [{label}] pid={pid}: ok={ok} err={err}  {last}")

    print("\n" + "-" * 92)
    overall = a_pass and i_pass
    if overall:
        print("VERDICT: PASS — no append dropped, no ion overwritten, ordinals/ions unique "
              "and contiguous under real concurrent writers. The conditional-put + "
              "local-advance retry closes the race.")
    else:
        bad = []
        if not a_pass:
            bad.append("appendOnVal")
        if not i_pass:
            bad.append("addIoSetVal")
        print(f"VERDICT: FAIL — the concurrent-append race is OPEN for: {', '.join(bad)}. "
              "Appends were dropped (errors > 0 / landed < expected / non-contiguous) "
              "and/or ions were silently overwritten (distinct values < expected). "
              "This is the pre-fix behavior.")
    print("=" * 92 + "\n")
    return overall


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--suffix", default="run1", help="table name suffix")
    ap.add_argument("--workers", type=int, default=8, help="concurrent OS-process writers")
    ap.add_argument("--appends", type=int, default=25, help="ops per worker per storm")
    ap.add_argument("--keep", action="store_true", help="skip teardown")
    ap.add_argument("--teardown-only", action="store_true",
                    help="only tear down (needs --suffix)")
    args = ap.parse_args()

    session = boto3.Session(region_name=args.region)
    ident = session.client("sts").get_caller_identity()
    print(f"account={ident['Account']}  caller={ident['Arn']}  region={args.region}")

    table = f"concurrent-append-probe-{args.suffix}"
    ddb = session.client("dynamodb")

    if args.teardown_only:
        print("teardown-only:")
        teardown(ddb, table)
        left = list_leftovers(ddb)
        print(f"  leftover concurrent-append-probe-* tables: {left if left else 'NONE'}")
        return 0

    print("\n[1/5] create table + GSI"); create_table(ddb, table)

    print(f"[2/5] appendOnVal storm: {args.workers} procs x {args.appends} appends")
    append_res = _run_storm(_append_worker, table, args.region, args.workers, args.appends)
    print(f"      done in {append_res['secs']:.1f}s  (ok={append_res['ok']}, err={append_res['err']})")

    print("[3/5] enumerate appended ordinals")
    append_rb = enumerate_append(table, args.region)

    print(f"[4/5] addIoSetVal storm: {args.workers} procs x {args.appends} adds")
    ioset_res = _run_storm(_ioset_worker, table, args.region, args.workers, args.appends)
    print(f"      done in {ioset_res['secs']:.1f}s  (ok={ioset_res['ok']}, err={ioset_res['err']})")

    print("[5/5] enumerate IoSet ions + report")
    ioset_rb = enumerate_ioset(table, args.region)
    ok = print_report(args.workers, args.appends, append_res, append_rb, ioset_res, ioset_rb)

    if args.keep:
        print(f"--keep set: leaving table {table}. Clean up later with:\n"
              f"  python probe.py --region {args.region} --teardown-only --suffix {args.suffix}")
    else:
        print("teardown:")
        teardown(ddb, table)
        left = list_leftovers(ddb)
        if left:
            print(f"  !! leftover concurrent-append-probe-* tables remain: {left}")
            return 2
        print("  verified: ZERO leftover concurrent-append-probe-* tables remain.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
