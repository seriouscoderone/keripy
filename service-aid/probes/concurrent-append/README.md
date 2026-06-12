# Concurrent-append race probe

Empirically proves the one thing moto/DynamoDB-Local **cannot** test about
keripy's `DynamoDBer` (`src/keri/db/dynamodbing.py`): **do `appendOnVal` and
`addIoSetVal` survive a real concurrent-writer race?**

## The race

`appendOnVal` and `addIoSetVal` pick the "next ordinal"/"next ion" by reading the
current max through the `subdb-index` GSI. A GSI is **always eventually
consistent** — there is no `ConsistentRead=True` for an index. Two Lambda
instances appending to the SAME key can both read the same stale max, compute the
same next ordinal, and collide.

- **Pre-fix `appendOnVal`** did one unconditional-ordinal `putVal` (a conditional
  put on `attribute_not_exists(PK)`) and, on collision, **raised `ValueError`** —
  the append was dropped.
- **Pre-fix `addIoSetVal`** computed `max_ion+1` once and did a `_put_item` with
  **no condition** — a colliding peer was **silently overwritten** (no error, but
  data lost).

**The fix** (this branch, `feat/ddb-concurrent-append`): both methods land via a
strongly-consistent conditional put and, on a `ConditionalCheckFailedException`,
**advance the ordinal/ion locally and retry**, bounded by `_APPEND_MAX_RETRY`
(64). No append is dropped; no ion is overwritten.

## Why a separate probe (and not a unit test)

moto and DynamoDB-Local update the GSI **synchronously**, so the stale-read
window never opens and every writer sees the true max — the race is unobservable.
This probe runs **N real OS processes** (`multiprocessing`, spawn), each with its
**own `DynamoDBer` instance / own boto3 client** (a separate "Lambda instance"),
all hammering the **same key** against a **real DynamoDB table**. It uses the
REAL shipped `DynamoDBer`, so it exercises the actual methods.

## What it does

1. Creates `concurrent-append-probe-<suffix>` — same schema as
   `KeriCoreStack.CoreTable` (PK/SK + `subdb-index` GSI on `gsi_pk`/`gsi_sk`,
   projection ALL, PAY_PER_REQUEST). Waits for the table **and** GSI to be ACTIVE.
2. **appendOnVal storm:** `--workers` (default 8) processes each do `--appends`
   (default 25) `appendOnVal` of distinct values to ONE key. After join it
   enumerates every ordinal under the key (via the real `getOnAllItemIter`) and
   asserts `landed == workers*appends` (zero drops), ordinals unique (zero
   overwrites), ordinals contiguous `0..N-1`. Each worker that catches an
   exception from `appendOnVal` increments an error counter.
3. **addIoSetVal storm:** same fan-out, each add a **globally-distinct** value
   (`f"{pid}-{i}"`) to ONE IoSet key. It enumerates the set (via the real
   `_get_ioset_raw`) and asserts all `workers*appends` distinct values are present
   (zero lost to overwrite) and ions unique/contiguous.
4. Prints a per-storm table (expected / landed / unique? / contiguous? / distinct
   values? / total worker errors) + a one-line VERDICT, tears the table down, and
   verifies **zero** `concurrent-append-probe-*` tables remain.

No IAM roles — the caller's own creds issue every call. Touches none of your
existing tables/stacks.

## Run

```bash
AWS_PROFILE=personal .venv/bin/python probe.py --region us-east-1
.venv/bin/python probe.py --region us-east-1 --workers 16 --appends 40   # heavier
.venv/bin/python probe.py --region us-east-1 --keep                      # leave table
.venv/bin/python probe.py --region us-east-1 --teardown-only --suffix run1
```

Requires creds for the target account with permission to create/delete a
DynamoDB table and to PutItem/Query/GetItem on it and its index. Exit 0 = PASS,
non-zero = the race is open (pre-fix behavior).

## Red → green protocol

Prove the probe actually detects the bug by running it against the PRE-fix
backend, then restoring the fix:

```bash
# RED: swap in the pre-fix backend from development, run, restore
git show development:src/keri/db/dynamodbing.py > src/keri/db/dynamodbing.py
AWS_PROFILE=personal .venv/bin/python probe.py --region us-east-1 --suffix redrun || true
git checkout -- src/keri/db/dynamodbing.py        # MUST restore the fixed file
git status                                         # confirm clean
.venv/bin/python -m pytest tests/db/test_dynamodbing.py -q   # 83 passed

# GREEN: post-fix
AWS_PROFILE=personal .venv/bin/python probe.py --region us-east-1
```

## Measured results (real AWS, account 117870855864, us-east-1, 8×25 = 200)

### GREEN (post-fix — `feat/ddb-concurrent-append`)

| storm        | expected | landed | unique | contiguous | distinct values | worker errors |
|--------------|---------:|-------:|--------|------------|-----------------|--------------:|
| appendOnVal  |      200 |    200 | YES    | YES        | —               |             0 |
| addIoSetVal  |      200 |    200 | YES    | YES        | YES (200/200)   |             0 |

VERDICT: **PASS** — no append dropped, no ion overwritten, ordinals/ions unique
and contiguous under real concurrent writers. (appendOnVal storm 18.2s,
addIoSetVal storm 19.7s; table + GSI torn down, zero leftovers; exit 0.)

### RED (pre-fix — `development`)

| storm        | expected | landed | unique | contiguous | distinct values | worker errors |
|--------------|---------:|-------:|--------|------------|-----------------|--------------:|
| appendOnVal  |      200 | **27** | YES    | **NO (gaps)** | —            | **173** (all `ValueError: Failed appending …`) |
| addIoSetVal  |      200 | **25** | YES    | **NO (gaps)** | **NO (25/200)** | 0          |

VERDICT: **FAIL** — the race is open for both methods.
- **appendOnVal**: 173 of 200 appends were **dropped** — each raised
  `ValueError: Failed appending val=… at key=…` (every worker hit it; some
  workers landed 0–1 of their 25). Only 27 survived, and they were non-contiguous.
- **addIoSetVal**: no errors raised, but only **25 of 200** values survived —
  175 were **silently overwritten** (the silent-data-loss signature: the
  unconditional put clobbered colliding peers with zero exceptions). Only 25
  distinct values present.

The pre-fix RED → post-fix GREEN swing (27→200 drops eliminated; 25→200 silent
overwrites eliminated) is the proof that the conditional-put + local-advance
retry closes the concurrent-append race.
