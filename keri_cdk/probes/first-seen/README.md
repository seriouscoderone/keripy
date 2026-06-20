# First-seen gate probe

Empirically proves the one thing moto/DynamoDB-Local **cannot** test about
keripy's first-seen gate (`src/keri/db/dynamodbing.py`): **does the generic
conditional `putVal` — not a process cap — enforce exactly-one-first-seen when N
concurrent writers race the same `(pre, sn)` slot?**

> **This probe is the only real-concurrency verification of the first-seen gate.
> CI covers single-threaded classification only (moto cannot reproduce true
> concurrent conditional races).**

## The gate

The first-seen gate stores the first-seen SAID for a given `(pre, sn)` in the
`fseen.` sub-database via the generic conditional `putVal`:

```python
db.putVal(fsdb, snKey(pre, sn), said)
```

`putVal` issues a `PutItem` with `attribute_not_exists(PK)` — only the **first**
writer wins; all subsequent writers receive a `ConditionalCheckFailedException`
and return `False`. The gate intentionally uses no process-level lock (e.g.
`reserved_concurrency=1`) — the conditional write IS the serialization primitive.

## Why a separate probe (and not a unit test)

moto and DynamoDB-Local evaluate conditional puts synchronously, so the race
window never opens: every loser trivially reads the winner because table state is
updated in-process before `getVal` runs — the race is unobservable.

This probe spawns **N real OS processes** (`multiprocessing`, spawn), each with
its **own `DynamoDBer` instance / own boto3 client** (a separate "Lambda
instance"), all racing against a **real DynamoDB table**. It uses the REAL
shipped `DynamoDBer` and the REAL `snKey` helper from `keri.db.dbing`.

**This probe requires real AWS credentials and a real DynamoDB table. It is NOT
suitable for CI (where moto is used) and must be run manually by an operator
before shipping the first-seen gate to production.**

## What it does

1. Creates `fsprobe-<suffix>` — same schema as `KeriCoreStack.CoreTable`
   (PK/SK + `subdb-index` GSI on `gsi_pk`/`gsi_sk`, projection ALL,
   PAY_PER_REQUEST). Waits for the table **and** GSI to be ACTIVE.

2. **Storm 1 — distinct saids (`sn=1`):** `--workers` (default 12) processes each
   propose a **unique** said (keyed by PID) for `(PRE, sn=1)` via `putVal`.
   Asserts: exactly **one** worker wins; every loser's `getVal` equals the single
   winning said. Outcome: proves no split-brain under a genuine race (not a
   degenerate same-value scenario).

3. **Storm 2 — same said (`sn=2`):** all workers propose the **same** said.
   Asserts: exactly **one** winner (putVal returns True once); every loser's
   `getVal` equals that shared said. Outcome: proves the idempotent race is safe
   (repeated proposals of the same value are benign).

4. **Storm 3 — recovery convergence via `setVal` (`sn=3`):** pre-seeds `(PRE, 3)`
   with an initial said, then N workers all call `setVal` to overwrite with
   `rot_said` (simulating a rotation/recovery write). Asserts every `getVal`
   returns `rot_said`. Outcome: proves the unconditional overwrite path converges
   under concurrent rotation.

5. Prints per-storm PASS/FAIL lines + a one-line VERDICT, tears the table down,
   and verifies zero `fsprobe-*` tables remain.

No IAM roles — the caller's own creds issue every call. Touches none of your
existing tables/stacks.

## Run

```bash
# Standard run: create table, run all three storms, teardown
AWS_PROFILE=personal .venv/bin/python probe.py --region us-east-1

# Heavier load
AWS_PROFILE=personal .venv/bin/python probe.py --region us-east-1 --workers 20 --suffix run2

# Leave table to inspect items after the run
AWS_PROFILE=personal .venv/bin/python probe.py --region us-east-1 --keep

# Tear down a leftover table from a previous --keep run
AWS_PROFILE=personal .venv/bin/python probe.py --region us-east-1 --teardown-only --suffix run1
```

Requires creds for the target account with permission to:
- Create and delete a DynamoDB table
- `PutItem`, `GetItem`, `Query` on the table and its GSI

Exit 0 = PASS (exactly-one-winner invariant holds). Non-zero = the first-seen
gate is OPEN or a storm failed to converge.

## Verdict meaning

| Verdict | Meaning |
|---------|---------|
| `PASS` | Exactly one first-seen winner under real concurrency; every loser observed the single winning said. The conditional `putVal` (not a process cap) enforces serializable first-seen per `(pre, sn)`. |
| `FAIL — Storm1` | More than one winner for distinct-said race: the conditional write did not serialize correctly. The first-seen invariant is **OPEN**. |
| `FAIL — Storm2` | More than one winner or loser reads diverged in the same-said race: idempotent race is not safe. |
| `FAIL — Storm3` | `setVal` concurrent writes did not converge: rotation/recovery is unsafe under concurrency. |

## CI vs. probe

| Scope | Mechanism | What it proves |
|-------|-----------|---------------|
| CI (`pytest`, moto) | Single-threaded, in-process | Correct classification logic: duplicate vs. LikelyDuplicitous, escrow routing, toad thresholds |
| **This probe** (real AWS, multiprocessing) | N OS processes, separate boto3 clients | **Conditional `putVal` — not a process cap — enforces exactly-one-first-seen under genuine concurrency** |

The probe is the only real-concurrency verification. CI moto tests are
complementary — they cover the KERI-layer routing but cannot reproduce the
DynamoDB conditional-write race.
