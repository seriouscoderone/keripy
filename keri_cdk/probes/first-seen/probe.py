#!/usr/bin/env python3
"""Real-AWS probe: N concurrent writers race the SAME (pre, sn) first-seen slot
via the generic conditional putVal. Proves the conditional write — not a process
cap — enforces exactly-one-first-seen. Needs real AWS (creds + a real table);
moto cannot reproduce true concurrent conditional races. NOT a CI gate.

keripy's DynamoDBer first-seen gate (src/keri/db/dynamodbing.py) stores the
first-seen SAID for a given (pre, sn) in the ``fseen.`` store via the generic
conditional ``putVal`` (a conditional put on ``attribute_not_exists(PK)``). Under
Lambda concurrency, N instances can race to write the first-seen slot for the
same (pre, sn). Only one must win; all losers must observe the SINGLE winning
said — that is the serializable first-seen invariant.

moto and DynamoDB-Local evaluate conditional puts synchronously, so the race
window never opens: every loser trivially reads the winner because table state
is updated in-process before ``getVal`` runs. This probe spawns N REAL OS
processes (multiprocessing, spawn), each with its OWN DynamoDBer instance / own
boto3 client (a separate "Lambda instance"), all racing against a REAL DynamoDB
table. It uses the REAL shipped DynamoDBer and the REAL snKey helper.

Two storms:
  - distinct-said storm: N workers each propose a UNIQUE said for (PRE, 1).
    Exactly one must win; every loser's getVal must equal the single winner's said.
  - same-said storm: N workers all propose the SAME said for (PRE, 2).
    Exactly one must win (putVal is False for the rest); every loser's getVal
    must equal that shared said (idempotent race).

Optional Storm 3 (recovery convergence): pre-seed (PRE, 3) via putVal, then N
workers all call setVal (unconditional overwrite) to rotate to rot_said; all
reads must converge to rot_said.

Everything is created with a unique ``fsprobe-<suffix>`` name and torn down at
the end (unless --keep). It touches NONE of your existing tables/stacks and
creates NO IAM roles (the caller's own creds issue every call).

Usage:
  python probe.py --region us-east-1                         # create, storm, teardown
  python probe.py --region us-east-1 --workers 12 --suffix run2
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

from keri.db.dbing import snKey

GSI_NAME = "subdb-index"
GSI_PK = "gsi_pk"
GSI_SK = "gsi_sk"

# DynamoDBer instance name == namespace; all workers MUST share it so they
# target the same partition for the same key.
PROBE_NAME = "fsprobe"
STORE = "fseen."
PRE = b"EprobePREAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


# ── worker entrypoints (each runs in its OWN OS process) ─────────────────────

def _distinct_worker(table_name, region, sn, q):
    """Each worker claims (PRE, sn) with a UNIQUE said -> exactly one must win."""
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    fsdb = db.env.open_db(STORE.encode())
    said = f"Esaid-{os.getpid():020d}".encode()[:44]
    won = db.putVal(fsdb, snKey(PRE, sn), said)
    existing = None if won else db.getVal(fsdb, snKey(PRE, sn))
    q.put((os.getpid(), bool(won), bytes(said),
           existing if existing is None else bytes(existing)))


def _same_worker(table_name, region, sn, said, q):
    """Every worker claims with the SAME said -> exactly one win, rest idempotent."""
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    fsdb = db.env.open_db(STORE.encode())
    won = db.putVal(fsdb, snKey(PRE, sn), said)
    existing = None if won else db.getVal(fsdb, snKey(PRE, sn))
    q.put((os.getpid(), bool(won),
           existing if existing is None else bytes(existing)))


def _setval_worker(table_name, region, sn, rot_said, q):
    """Recovery convergence: N workers setVal to rot_said -> all reads == rot_said."""
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    fsdb = db.env.open_db(STORE.encode())
    db.setVal(fsdb, snKey(PRE, sn), rot_said)
    result = db.getVal(fsdb, snKey(PRE, sn))
    q.put((os.getpid(), bytes(result) if result is not None else None))


def _run(target, args_tuple, workers):
    """Spawn `workers` processes, join, collect results."""
    ctx = mp.get_context("spawn")  # fresh interpreter per worker = fresh client
    q = ctx.Queue()
    procs = [ctx.Process(target=target, args=args_tuple + (q,))
             for _ in range(workers)]
    t0 = time.perf_counter()
    for p in procs:
        p.start()
    rows = [q.get() for _ in procs]
    for p in procs:
        p.join()
    dt = time.perf_counter() - t0
    return rows, dt


# ── table lifecycle (copied from concurrent-append/probe.py) ─────────────────

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
    # GSI must become ACTIVE too.
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


def list_leftovers(ddb, prefix="fsprobe-"):
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

def main():
    ap = argparse.ArgumentParser(
        description="Real-AWS N-writer first-seen probe (generic putVal gate)")
    ap.add_argument("--region", required=True, help="AWS region")
    ap.add_argument("--suffix", default="run1", help="table name suffix")
    ap.add_argument("--workers", type=int, default=12,
                    help="concurrent OS-process writers per storm (default 12)")
    ap.add_argument("--keep", action="store_true", help="skip teardown")
    ap.add_argument("--teardown-only", action="store_true",
                    help="only tear down an existing probe table (needs --suffix)")
    args = ap.parse_args()

    session = boto3.Session(region_name=args.region)
    ident = session.client("sts").get_caller_identity()
    print(f"account={ident['Account']}  caller={ident['Arn']}  region={args.region}")

    table_name = f"{PROBE_NAME}-{args.suffix}"
    ddb = session.client("dynamodb")

    if args.teardown_only:
        print("teardown-only:")
        teardown(ddb, table_name)
        left = list_leftovers(ddb)
        print(f"  leftover fsprobe-* tables: {left if left else 'NONE'}")
        return 0

    # ── provision ────────────────────────────────────────────────────────────
    print("\n[1/5] create table + GSI")
    create_table(ddb, table_name)

    # ── Storm 1: distinct saids @sn=1 ────────────────────────────────────────
    print(f"\n[2/5] Storm 1 — distinct saids: {args.workers} workers race (PRE, sn=1)")
    d, dt1 = _run(_distinct_worker, (table_name, args.region, 1), args.workers)
    wins = [r for r in d if r[1]]
    losers = [r for r in d if not r[1]]
    winner_said = wins[0][2] if len(wins) == 1 else None
    loser_existing = {r[3] for r in losers}
    storm1 = (len(wins) == 1 and loser_existing == {winner_said})
    print(f"  done in {dt1:.1f}s  winners={len(wins)}  losers={len(losers)}")
    if len(wins) == 1:
        print(f"  winner said : {winner_said}")
        print(f"  loser reads : {loser_existing}")
        print(f"  Storm 1 : {'PASS' if storm1 else 'FAIL — loser reads diverge'}")
    else:
        print(f"  Storm 1 : FAIL — {len(wins)} winners (expected exactly 1)")
        for r in wins:
            print(f"    pid={r[0]} said={r[2]}")

    # ── Storm 2: same said @sn=2 ─────────────────────────────────────────────
    print(f"\n[3/5] Storm 2 — same said: {args.workers} workers race (PRE, sn=2)")
    same_said = b"Esame-said-shared-across-all-workers-AAAAAAA"[:44]
    s, dt2 = _run(_same_worker, (table_name, args.region, 2, same_said), args.workers)
    s_wins = [r for r in s if r[1]]
    s_losers = [r for r in s if not r[1]]
    # losers: r[2] is existing (bytes); wins: r[2] is existing (None, because won=True skips getVal)
    loser_vals = {r[2] for r in s_losers}
    storm2 = (len(s_wins) == 1 and loser_vals == {same_said})
    print(f"  done in {dt2:.1f}s  winners={len(s_wins)}  losers={len(s_losers)}")
    print(f"  loser reads : {loser_vals}")
    print(f"  Storm 2 : {'PASS' if storm2 else 'FAIL'}")

    # ── Storm 3 (optional): recovery convergence via setVal @sn=3 ───────────
    print(f"\n[4/5] Storm 3 — recovery convergence: pre-seed (PRE, sn=3) then setVal")
    # Pre-seed with an initial said.
    from keri.db.dynamodbing import DynamoDBer
    db0 = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                          table_name=table_name, region=args.region)
    fsdb0 = db0.env.open_db(STORE.encode())
    init_said = b"Einit-seed-said-AAAAAAAAAAAAAAAAAAAAAAAAAA"[:44]
    db0.putVal(fsdb0, snKey(PRE, 3), init_said)
    rot_said = b"Erot-said-convergence-AAAAAAAAAAAAAAAAAAAAAA"[:44]
    v, dt3 = _run(_setval_worker, (table_name, args.region, 3, rot_said), args.workers)
    all_converge = all(r[1] == rot_said for r in v)
    storm3 = all_converge
    print(f"  done in {dt3:.1f}s")
    print(f"  Storm 3 : {'PASS — all reads == rot_said' if storm3 else 'FAIL — reads diverged'}")
    if not storm3:
        for r in v:
            if r[1] != rot_said:
                print(f"    pid={r[0]} got={r[1]!r} expected={rot_said!r}")

    # ── verdict ───────────────────────────────────────────────────────────────
    overall = storm1 and storm2 and storm3
    print("\n" + "=" * 80)
    print("FIRST-SEEN GATE PROBE — RESULTS")
    print(f"  {args.workers} OS-process workers  |  table={table_name}  |  region={args.region}")
    print("=" * 80)
    print(f"  Storm 1 (distinct saids @sn=1) : {'PASS' if storm1 else 'FAIL'}")
    print(f"  Storm 2 (same said    @sn=2)   : {'PASS' if storm2 else 'FAIL'}")
    print(f"  Storm 3 (setVal conv. @sn=3)   : {'PASS' if storm3 else 'FAIL'}")
    print("-" * 80)
    if overall:
        print("VERDICT: PASS — exactly one first-seen winner under real concurrency; "
              "every loser observed the single winning said. The conditional putVal "
              "(not a process cap) enforces serializable first-seen per (pre, sn).")
    else:
        storms_failed = [s for s, ok in [("Storm1", storm1), ("Storm2", storm2),
                                          ("Storm3", storm3)] if not ok]
        print(f"VERDICT: FAIL — {', '.join(storms_failed)} failed. "
              "More than one winner or losers observed divergent saids = "
              "the first-seen invariant is OPEN.")
    print("=" * 80 + "\n")

    # ── teardown ──────────────────────────────────────────────────────────────
    if args.keep:
        print(f"--keep set: leaving table {table_name}. Clean up later with:\n"
              f"  python probe.py --region {args.region} --teardown-only --suffix {args.suffix}")
    else:
        print("teardown:")
        teardown(ddb, table_name)
        left = list_leftovers(ddb)
        if left:
            print(f"  !! leftover fsprobe-* tables remain: {left}")
            return 2
        print("  verified: ZERO leftover fsprobe-* tables remain.")

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
