# GSI read-after-write staleness probe

Empirically measures the one production-gating question behind keripy's
`DynamoDBer` (`src/keri/db/dynamodbing.py`): **how stale is an immediate GSI
read-after-write, and how long does it take to converge?**

`DynamoDBer` serves every *ordered/range* read through a `subdb-index` GSI:
KEL-by-sequence-number, "get the latest event" (the `appendOnVal` / getLast
pattern = `_query_gsi(..., forward=False)` then `items[0]`), escrow scans, and
counts. DynamoDB GSIs are **always eventually consistent** — there is no
`ConsistentRead=True` for an index — so a read issued right after the write that
should populate it can miss the row or return a stale "latest".

**moto / DynamoDB-Local report zero staleness** because they update indexes
synchronously, so they cannot answer this question. This probe runs against real
AWS, creates throwaway resources, measures, and tears them down. It touches none
of your existing tables/stacks and creates **no IAM roles** (the caller's own
creds issue every call).

## What it does

1. Creates `gsi-stale-probe-<suffix>` — same schema as `KeriCoreStack.CoreTable`
   (PK/SK + `subdb-index` GSI on `gsi_pk`/`gsi_sk`, projection ALL, PAY_PER_REQUEST).
2. Simulates a KEL append for `ns="probe:kel"`, subdb `kels`, writing seq
   `0..N-1` with DynamoDBer's **real key shapes**:
   - `PK = "{ns}#{subdb}#{hex(onkey)}"`, `SK = "V"`,
   - `gsi_pk = "{ns}#{subdb}"`, `gsi_sk = "{hex(onkey)}"`,
   - `onkey = basekey + b"." + b"%032x" % on` (32-hex zero-padded ordinal, so the
     hex sorts lexically in write order — exactly how `getLast` reads "latest").
3. Per write, takes **two GSI measurements + one control**:
   - **exact-item visibility:** immediately `Query gsi_sk == just-written`; on
     MISS, tight-poll (5 ms, cap 2 s) until it appears and record catch-up ms.
   - **get-latest correctness:** immediately `Query` newest-first `Limit=1`;
     FRESH if the newest `gsi_sk` decodes to the just-written ordinal, else STALE
     (records how many events behind). This is the one that bites "read the
     latest key state right after writing it."
   - **control:** strongly-consistent base-table `GetItem(ConsistentRead=True)`
     of the row just written — must ALWAYS hit (sanity that point reads are fine).
4. **Bursty pass:** ~`burst` back-to-back writes with no delay, then one GSI
   get-latest — does it see the last write?
5. Prints miss% / stale% / catch-up p50/p90/p99/max / control misses / bursty
   result + a one-line VERDICT, then tears down and verifies **zero**
   `gsi-stale-probe-*` tables remain.

## Run

```bash
.venv/bin/python probe.py --region us-east-1                 # create, measure, teardown
.venv/bin/python probe.py --region us-east-1 --keep          # leave the table to inspect
.venv/bin/python probe.py --region us-east-1 --teardown-only --suffix <suffix>
.venv/bin/python probe.py --region us-east-1 --iters 800 --burst 100   # heavier
```

```bash
AWS_PROFILE=personal .venv/bin/python probe.py --region us-east-1
```

Requires credentials for the target account with permission to create/delete a
DynamoDB table and to Query/PutItem/GetItem on it and its index. No IAM roles.

## Interpreting the result (IMPORTANT)

This probe is **single-threaded and sequential**: each iteration does
put → exact-query → latest-query → strong-get, all synchronous round-trips. The
network round-trip alone (tens of ms) usually gives the GSI enough time to
propagate *before the next read*, so observed staleness on a quiet table is
**low and bursty** — a representative run sees the occasional exact-item MISS
catching a real ~100–150 ms propagation window, and get-latest rarely or never
observed stale (it is issued *after* the exact-query, so even more time has
elapsed). A clean 0%/0% run does **not** mean the GSI is strongly consistent:
the index is eventually consistent **by contract**, and absence of observed
staleness here is low-load luck, not a guarantee. Under concurrent writers, hot
partitions, or a read issued with *zero* intervening latency, staleness will be
higher. The actionable conclusion is unchanged: **do not serve read-after-write
"latest key state" off the GSI** — use a strongly-consistent base-table read for
correctness-critical "latest" lookups.
