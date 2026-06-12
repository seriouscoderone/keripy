# DynamoDBer Concurrent-Append Hardening — Design (Phase A)

**Status:** approved 2026-06-12
**Branch:** `feat/ddb-concurrent-append` (worktree `.worktrees/ddb-concurrency`, off `development`)
**Scope:** Phase A of the larger SAM→CDK / pooled-core-table effort. This phase is pure
keripy backend (`src/keri/db/dynamodbing.py`) — no infrastructure, no CDK, no handler
changes. It benefits the existing live SAM witness/mailbox immediately and is a prerequisite
for safely running any horizontally-scaled (multi-Lambda-instance) writer.

## Problem

`DynamoDBer` (the LMDBer-compatible DynamoDB backend) assumes a single writer per logical
key-stream — true for LMDB (single-writer/multi-reader), but NOT for a multi-instance Lambda.
Two concurrent writers appending to the *same* key compute the next ordinal from the
`subdb-index` **GSI**, which is eventually consistent (read-after-write lag measured at up to
~130 ms; unbounded by contract). Both can read the same stale max ordinal and collide. Two
methods are affected:

1. **`appendOnVal`** (`dynamodbing.py:661-695`) — computes `on` from a GSI reverse-query max,
   then does ONE conditional `putVal` (`attribute_not_exists(PK)`). On collision it **raises
   `ValueError("Failed appending ...")`** — the append is **dropped, not retried**. Reachable
   via the mailbox `tpcs.append` deposit path (`storing.py:128`); the mailbox is explicitly a
   multi-instance Lambda, so two concurrent `/fwd` deposits to the same recipient topic can
   drop a message. Also `.fels` first-seen append.

2. **`addIoSetVal`** (`dynamodbing.py:916-938`) — dedup-check + `max_ion` both from the GSI
   (eventual), then a **final `_put_item` with NO condition** → two concurrent adds for the
   same `(key)` computing the same `ion` **silently overwrite** one another (one value lost).
   Reachable via `.wigs`/`.rcts` IoSet adds. For `.wigs` the loss is masked downstream by
   witness-index dedup in `verifySigs`, but the unconditional overwrite on a shared key is a
   latent data-loss bug.

Neither is a correctness hazard for *event acceptance* (toad satisfaction uses the in-memory
verified wigers list, not a GSI read — confirmed in the read-after-write audit, 2026-06-11),
but both are real **data-loss-under-concurrency** bugs on the append/add path.

moto and DynamoDB-Local CANNOT surface either bug — their GSI is read-after-write consistent,
so the collision window is invisible. This is why a real-AWS probe is required (§Verification).

## Design

### Core mechanic: local-increment-on-collision (NOT re-query)

When a conditional put collides (another writer already took this ordinal), do **not**
re-query the lagging GSI. Instead, advance the ordinal locally (`on += 1`) and retry the
conditional put, repeating until one succeeds or a bounded retry ceiling is hit.

Rationale: the conditional `put_item` is a **strongly-consistent base-table** operation, so it
always lands on the first genuinely-free slot regardless of GSI staleness. The GSI query
remains only a *starting-point estimate* for the first attempt (avoids starting from 0 every
time). Concurrent appends therefore all land — contiguous, none dropped, none overwritten — in
arrival order. Arrival-order best-effort is the correct semantics for `.fels`/`.tpcs` (neither
requires that a specific writer win a specific ordinal, only that every appended value persists
at *some* unique ordinal).

A new module constant bounds the loop:

```python
_APPEND_MAX_RETRY = 64   # contention ceiling; exceeding it signals a real anomaly, not normal load
```

64 is far above any realistic concurrent-writer fan-in for a single key (KERI per-key write
rates are low); hitting it means something is wrong (e.g. a hot-key storm or a bug), so raising
there is correct.

### Fix 1 — `appendOnVal`

Keep the existing GSI-max computation to derive the **starting** `on`. Replace the single
`putVal`-or-raise tail with a bounded retry loop:

```python
        # ... existing GSI-max logic computes starting `on` ...
        for _ in range(_APPEND_MAX_RETRY):
            if on >= MaxON:              # preserve the original overflow guard (cn >= MaxON)
                raise ValueError(f"Number part {on=} for key part {key=} exceeds maximum size.")
            if self.putVal(db=db, key=onKey(key, on, sep=sep), val=val):
                return on
            on += 1                      # collision: another writer took `on`; try the next slot
        raise ValueError(
            f"Failed appending {val=} at {key=} after {_APPEND_MAX_RETRY} attempts (excessive contention)."
        )
```

Behavioral contract unchanged for the single-writer case (returns the new ordinal). Under
contention it now returns the next free ordinal instead of raising. The `MaxON` guard is
preserved inside the loop.

### Fix 2 — `addIoSetVal`

Add the no-overwrite condition to the final put and wrap it in the same local-increment loop.
The dedup pre-check stays best-effort (a perfect cross-writer dedup would require a DynamoDB
transaction — explicitly out of scope; documented in code). The load-bearing change is
**eliminating the silent overwrite**:

```python
        # ... existing dedup pre-check + GSI max_ion logic computes starting `ion` ...
        for _ in range(_APPEND_MAX_RETRY):
            iokey = suffix(key, ion, sep=sep)
            if self._put_item(db, iokey, _SK_SINGLE, val, gsi_sk=_hex(iokey),
                              condition="attribute_not_exists(PK)"):
                return True
            ion += 1                     # collision: ion taken; advance
        raise ValueError(
            f"Failed adding IoSet val at {key=} after {_APPEND_MAX_RETRY} attempts (excessive contention)."
        )
```

`_put_item` already returns `False` on `ConditionalCheckFailedException` (dynamodbing.py:380-383),
so threading the condition through is the only change beyond the loop. (Note: `addIoSetVal`
returns `bool` — `True` on add, `False` on duplicate — so the success path returns `True`; the
contention-exhausted path raises, consistent with Fix 1.)

### Out of scope (explicit)

- Cross-writer **transactional dedup** for IoSet — best-effort dedup retained; documented.
- Any **per-KEL sequence-number coordination** (two writers appending *different valid* events
  at the next sn of the same KEL). That is a higher-layer concern (Manager/Kever), not a
  `dynamodbing` KV-primitive concern, and is addressed by the single-writer reserved-concurrency
  decision in Phase B, not here.
- The false-404 synchronous responders — deferred to Phase B (handler-layer retry), per the
  tight-scope decision.
- LMDB backend — untouched (it already has correct single-writer semantics).

## Verification

### Unit tests (moto) — `tests/db/test_dynamodbing.py`

Deterministically *simulate* a collision (moto can't produce a real race, but it executes the
conditional-put + retry logic faithfully):

- **`appendOnVal` retries past a taken ordinal:** pre-insert the item at the ordinal
  `appendOnVal` would compute (so its first conditional put fails), call `appendOnVal`, assert
  it returns `start_on + 1` and the value is stored there (not raised).
- **`appendOnVal` retries past several taken ordinals:** pre-insert a contiguous run, assert it
  lands at the first free slot above the run.
- **`addIoSetVal` no longer overwrites:** pre-insert at the computed `ion`, call `addIoSetVal`,
  assert the pre-existing value still present AND the new value stored at `ion+1` (the
  pre-fix code would have overwritten).
- **`addIoSetVal` conditional collision retries:** assert it advances `ion` on collision.
- **Single-writer regression:** the normal no-contention path still returns the expected
  ordinal / `True` and stores at the expected key (guards against the loop changing happy-path
  behavior).
- All existing `test_dynamodbing.py` tests stay green.

### Real-AWS concurrent probe — `service-aid/probes/concurrent-append/`

Structural template: the existing `service-aid/probes/{leadingkeys,gsi-staleness}/probe.py`
(same resource-naming `concurrent-append-probe-<suffix>`, setup/teardown, leftover-verification,
report formatting, `--keep`/`--teardown-only --suffix` flags, `AWS_PROFILE=personal`,
us-east-1, account 117870855864 verified via `sts.get_caller_identity`).

- Creates a throwaway table with the core schema (PK/SK + `subdb-index` GSI on gsi_pk/gsi_sk,
  PROJECTION ALL, PAY_PER_REQUEST).
- Spawns **N concurrent worker processes** (`multiprocessing`, default N=8), each with its OWN
  `DynamoDBer` instance (separate boto3 client — mirrors separate Lambda instances), all
  calling `appendOnVal` on the **same** `(namespace, subdb, key)` **M times** (default M=25),
  for `N×M` total appends. A second pass does the same against `addIoSetVal`.
- After the storm, queries all items for the key and asserts:
  - exactly `N×M` items landed (zero drops),
  - ordinals are unique (zero overwrites),
  - ordinals are contiguous `0..N×M-1` (no gaps — confirms local-increment packs them).
- **Red→green protocol** (documented in README): run on the **pre-fix** `dynamodbing.py` to
  demonstrate drops (`appendOnVal` raises in workers) / overwrites (`addIoSetVal` count <
  N×M), then on the **post-fix** to demonstrate clean `N×M`/unique/contiguous. The probe
  imports `DynamoDBer` from the worktree's `src/`, so checking out pre/post-fix changes what it
  tests. The probe is the empirical receipt that the race is closed.
- Tears the table down; verifies zero `concurrent-append-probe-*` leftovers via `list_tables`.
  No IAM roles needed (caller creds suffice; the LeadingKeys ARN/em-dash gotcha does not apply).

## Files

- Modify: `src/keri/db/dynamodbing.py` (the two methods + `_APPEND_MAX_RETRY` constant)
- Modify: `tests/db/test_dynamodbing.py` (new concurrency-logic tests)
- Create: `service-aid/probes/concurrent-append/probe.py`
- Create: `service-aid/probes/concurrent-append/README.md`

## Execution

Subagent-driven (implementer + spec-compliance review + code-quality review per task), then
final whole-branch review, then merge to `development` (direct, matching the prior keripy
backend work). The probe is committed but its real-AWS run is invoked/inspected by the
controller (the user pre-authorized real-AWS testing on `personal`).
