# DynamoDBer Concurrent-Append Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `DynamoDBer.appendOnVal` and `addIoSetVal` safe under concurrent writers (multiple Lambda instances) so no append is dropped or silently overwritten.

**Architecture:** Both methods compute a starting ordinal from the eventually-consistent `subdb-index` GSI, then write with a *strongly-consistent* conditional `put_item`. On a conditional collision, advance the ordinal locally (`+1`) and retry — never re-query the lagging GSI — bounded by `_APPEND_MAX_RETRY=64`. Correctness comes from the conditional base-table put always landing on the first genuinely-free slot regardless of GSI staleness.

**Tech Stack:** Python, boto3/DynamoDB, moto (unit), multiprocessing + real AWS (concurrency probe). keripy fork.

**Spec:** `docs/superpowers/specs/2026-06-12-ddb-concurrent-append-design.md`

---

## File Structure

- `src/keri/db/dynamodbing.py` — add `_APPEND_MAX_RETRY` constant; rewrite the tails of `appendOnVal` (~661-695) and `addIoSetVal` (~916-938). Single responsibility: the KV backend primitives.
- `tests/db/test_dynamodbing.py` — add concurrency-logic unit tests (moto, monkeypatched stale-max to force collisions). Existing tests stay green.
- `service-aid/probes/concurrent-append/probe.py` + `README.md` — real-AWS N-process concurrency probe; mirrors `service-aid/probes/{leadingkeys,gsi-staleness}/`.

---

## Task 0: Worktree venv + clean baseline

**Files:** none (environment setup)

- [ ] **Step 1: Create the venv and install keripy editable + test deps**

Run (from the worktree root `~/code/keripy/.worktrees/ddb-concurrency`):
```bash
python3 -m venv .venv
.venv/bin/pip install -q -e . moto boto3 pytest pytest-asyncio
```
(`.venv` is gitignored. `-e .` installs keripy so `from keri.db.dynamodbing import ...` resolves.)

- [ ] **Step 2: Verify the existing dynamodbing suite is green (baseline)**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing.py -q`
Expected: all pass (this is the pre-change baseline; ~the suite that exercises `appendOnVal`/`addIoSetVal` normal paths). If anything fails here, STOP and report — do not start changes on a red baseline.

---

## Task 1: `appendOnVal` local-increment-on-collision retry

**Files:**
- Modify: `src/keri/db/dynamodbing.py` (add `_APPEND_MAX_RETRY` near the other module constants ~line 117-128; rewrite `appendOnVal` ~661-695)
- Test: `tests/db/test_dynamodbing.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/db/test_dynamodbing.py` (inside the On-methods test class, near `test_appendOnVal` ~line 211; `dber` fixture + `STORES` already exist). This monkeypatches `_query_gsi` to return a stale-empty max so the starting ordinal collides with real items already at 0 and 1, forcing the retry loop:

```python
    def test_appendOnVal_retries_past_taken_ordinals(self, dber):
        """Under a stale GSI (concurrent-writer race), appendOnVal must land at the
        first genuinely-free ordinal via conditional-put retry, not raise/overwrite."""
        sdb = dber.env.open_db(b"snk.")
        # Two events already appended (real base-table items at on=0, on=1).
        assert dber.appendOnVal(sdb, b"pre", val=b"evt0") == 0
        assert dber.appendOnVal(sdb, b"pre", val=b"evt1") == 1
        # Simulate GSI lag: the max-ordinal query reports the set as empty,
        # so the method starts at on=0 (which is already taken).
        dber._query_gsi = lambda *a, **k: []
        on = dber.appendOnVal(sdb, b"pre", val=b"evt2")
        assert on == 2                                   # advanced past taken 0 and 1
        assert dber.getOnVal(sdb, b"pre", on=0) == b"evt0"  # untouched
        assert dber.getOnVal(sdb, b"pre", on=1) == b"evt1"  # untouched
        assert dber.getOnVal(sdb, b"pre", on=2) == b"evt2"  # landed at first free slot
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing.py::TestDynamoDBerOnMethods::test_appendOnVal_retries_past_taken_ordinals -q`
(Adjust the class name to the actual On-methods test class in the file.)
Expected: FAIL — the pre-change `appendOnVal` does a single conditional `putVal` at the stale `on=0`, which collides, and raises `ValueError("Failed appending ...")`.

- [ ] **Step 3: Add the constant**

In `src/keri/db/dynamodbing.py`, near the other module-level constants (after `_GSI_SK` ~line 128):
```python
_APPEND_MAX_RETRY = 64   # ordinal-collision retry ceiling; exceeding it signals a real
                         # anomaly (hot-key storm / bug), not normal contention
```

- [ ] **Step 4: Rewrite the tail of `appendOnVal`**

Replace the final `if not self.putVal(...): raise ...` / `return on` block (~693-695) with the bounded local-increment loop. The GSI-max logic above it (computing the starting `on`) is unchanged:
```python
        # `on` here is the starting estimate from the (eventually-consistent) GSI max.
        # Land at the first genuinely-free ordinal via strongly-consistent conditional
        # puts, advancing locally on collision — robust to GSI staleness and concurrent
        # writers (neither dropped nor overwritten; arrival-order best-effort).
        for _ in range(_APPEND_MAX_RETRY):
            if on >= MaxON:
                raise ValueError(
                    f"Number part {on=} for key part {key=} exceeds maximum size.")
            if self.putVal(db=db, key=onKey(key, on, sep=sep), val=val):
                return on
            on += 1
        raise ValueError(
            f"Failed appending {val=} at {key=} after {_APPEND_MAX_RETRY} attempts "
            "(excessive contention).")
```
(Note: the pre-existing `MaxON` guard that raised during the GSI-max computation can stay; the in-loop `on >= MaxON` guard preserves the same overflow semantics for the retry path.)

- [ ] **Step 5: Run the new test + the existing appendOnVal tests**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing.py -k appendOnVal -q`
Expected: PASS — the new retry test plus all existing `test_appendOnVal*` (single-writer regression: sequential 0,1,2 still returned; empty-key/None-val still raise).

- [ ] **Step 6: Commit**
```bash
git add src/keri/db/dynamodbing.py tests/db/test_dynamodbing.py
git commit -m "fix(dynamodbing): appendOnVal local-increment retry on conditional collision (concurrent-writer safe)"
```

---

## Task 2: `addIoSetVal` conditional put + retry

**Files:**
- Modify: `src/keri/db/dynamodbing.py` (rewrite the tail of `addIoSetVal` ~916-938)
- Test: `tests/db/test_dynamodbing.py`

- [ ] **Step 1: Write the failing test**

Add near `test_addIoSetVal` (~line 411). Monkeypatch `_get_ioset_raw` (the `max_ion` source) to report the set as empty so the computed `ion` collides with a real item already at `ion=0`, and `_get_ioset_items` (the dedup source) to empty so the dedup pre-check passes:

```python
    def test_addIoSetVal_does_not_overwrite_on_stale_max(self, dber):
        """Under a stale GSI (concurrent-writer race), addIoSetVal must NOT overwrite an
        existing ion — it advances via conditional put. (Pre-fix: unconditional put
        silently overwrote the value at the colliding ion.)"""
        sdb = dber.env.open_db(b"set.")
        assert dber.addIoSetVal(sdb, b"k", b"first") is True      # real item at ion=0
        # Simulate GSI lag: dedup + max-ion queries report the set as empty,
        # so the method computes ion=0 (already taken by b"first").
        dber._get_ioset_raw = lambda *a, **k: iter(())
        dber._get_ioset_items = lambda *a, **k: iter(())
        assert dber.addIoSetVal(sdb, b"k", b"second") is True     # must advance, not overwrite
        vals = [v for _, v in dber.getIoSetItemIter(sdb, b"k")]
        assert b"first" in vals      # NOT overwritten
        assert b"second" in vals     # landed at the next free ion
        assert len(vals) == 2
```
(Confirm the exact `_get_ioset_raw`/`_get_ioset_items` names + return shapes against the source; both are generators/iterables of `(ion, val)` and `(key, val)` respectively — match them.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing.py -k addIoSetVal_does_not_overwrite -q`
Expected: FAIL — the pre-change `addIoSetVal` does an UNCONDITIONAL `_put_item` at the stale `ion=0`, overwriting `b"first"` with `b"second"`; the final iter then has only one value, so `len(vals) == 2` fails (and `b"first"` is gone).

- [ ] **Step 3: Rewrite the tail of `addIoSetVal`**

Replace the `ion = max_ion + 1` / `iokey = ...` / unconditional `self._put_item(...)` / `return True` block (~935-938) with the conditional + local-increment loop. The dedup pre-check (~926-929) and `max_ion` computation (~932-933) stay:
```python
        # Land at the first free ion via strongly-consistent conditional puts, advancing
        # locally on collision — no silent overwrite under concurrent writers / GSI lag.
        ion = max_ion + 1
        for _ in range(_APPEND_MAX_RETRY):
            iokey = suffix(key, ion, sep=sep)
            if self._put_item(db, iokey, _SK_SINGLE, val, gsi_sk=_hex(iokey),
                              condition="attribute_not_exists(PK)"):
                return True
            ion += 1
        raise ValueError(
            f"Failed adding IoSet val at {key=} after {_APPEND_MAX_RETRY} attempts "
            "(excessive contention).")
```
(`_put_item` already returns `False` on `ConditionalCheckFailedException` — dynamodbing.py:380-383 — so only the `condition=` kwarg + loop are new. Cross-writer *dedup* stays best-effort by design; the fix's job is eliminating the overwrite.)

- [ ] **Step 4: Run the new test + existing IoSet tests**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing.py -k "IoSet or ioset" -q`
Expected: PASS — the new no-overwrite test plus all existing `test_addIoSetVal`/`test_*IoSet*` (single-writer regression: add/dedup/last/cnt unchanged).

- [ ] **Step 5: Full dynamodbing suite (no regressions)**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**
```bash
git add src/keri/db/dynamodbing.py tests/db/test_dynamodbing.py
git commit -m "fix(dynamodbing): addIoSetVal conditional put + local-increment retry (no silent overwrite)"
```

---

## Task 3: Real-AWS concurrent-writer probe

**Files:**
- Create: `service-aid/probes/concurrent-append/probe.py`
- Create: `service-aid/probes/concurrent-append/README.md`

Mirror the STRUCTURE of `service-aid/probes/gsi-staleness/probe.py` (read it first): same `argparse` (`--region`, `--suffix`, `--keep`, `--teardown-only`), `sts.get_caller_identity` preflight (expect account `117870855864`, user `joseph`), table create/wait, teardown + `list_tables` leftover-verification, report formatting. Resource name `concurrent-append-probe-<suffix>`. No IAM roles needed (caller creds suffice).

- [ ] **Step 1: Build the probe**

The probe opens a real table with the core schema (PK/SK + `subdb-index` GSI on `gsi_pk`/`gsi_sk`, PROJECTION ALL, PAY_PER_REQUEST), then runs concurrent workers using the REAL `DynamoDBer` so it tests the actual shipped methods. Worker + driver shape:

```python
import multiprocessing as mp
from keri.db.dynamodbing import DynamoDBer

def _worker(table_name, region, key, count, q):
    # Each worker = its own DynamoDBer / boto3 client = a separate "Lambda instance".
    db = DynamoDBer.open(name="probe", stores=["evts."], table_name=table_name, region=region)
    sdb = db.env.open_db(b"evts.")
    ok, err = 0, 0
    for i in range(count):
        try:
            db.appendOnVal(sdb, key, val=f"{mp.current_process().pid}-{i}".encode())
            ok += 1
        except Exception as e:        # pre-fix: ValueError("Failed appending ...") on collision
            err += 1
    q.put((ok, err))

def run_append_storm(table_name, region, n_workers, per_worker):
    key = b"hotkey"
    q = mp.Queue()
    procs = [mp.Process(target=_worker, args=(table_name, region, key, per_worker, q))
             for _ in range(n_workers)]
    for p in procs: p.start()
    for p in procs: p.join()
    reported_ok = sum(q.get()[0] for _ in procs)
    # Inspect what actually landed: query the GSI for all ordinals under `key`.
    db = DynamoDBer.open(name="probe", stores=["evts."], table_name=table_name, region=region)
    sdb = db.env.open_db(b"evts.")
    ons = sorted(on for on, _ in db.getOnItemIter(sdb, key))   # confirm against real accessor
    expected = n_workers * per_worker
    return {
        "expected": expected,
        "landed": len(ons),
        "unique": len(set(ons)) == len(ons),
        "contiguous": ons == list(range(expected)),
        "reported_ok": reported_ok,
    }
```
Defaults: `N_WORKERS=8`, `PER_WORKER=25` (→ 200 appends to one hot key). Add an equivalent `run_ioset_storm` doing concurrent `addIoSetVal(sdb, key, val=<unique per call>)` and asserting all `N×M` distinct values are present and ion-unique. Verify `getOnItemIter`/`getIoSetItemIter` are the right read accessors against the source (adjust names if needed).

PASS criteria (report + non-zero exit on failure): `landed == expected`, `unique`, `contiguous`, and (post-fix) `reported_ok == expected` with zero worker errors.

- [ ] **Step 2: Post-fix run (GREEN)**

Run: `AWS_PROFILE=personal .venv/bin/python service-aid/probes/concurrent-append/probe.py --region us-east-1`
Expected: `landed == expected (200)`, unique, contiguous, zero worker errors → VERDICT clean. Confirm zero `concurrent-append-probe-*` leftovers after teardown.

- [ ] **Step 3: Pre-fix run (RED) — demonstrate the bug**

Temporarily restore the pre-fix backend, run, then restore the fix:
```bash
git show development:src/keri/db/dynamodbing.py > src/keri/db/dynamodbing.py   # pre-fix version
AWS_PROFILE=personal .venv/bin/python service-aid/probes/concurrent-append/probe.py --region us-east-1 --suffix redrun || true
git checkout -- src/keri/db/dynamodbing.py                                     # restore the fix
```
Expected (RED): worker errors > 0 (appendOnVal `ValueError` on collision) and/or `landed < expected` / not contiguous (dropped appends); the IoSet storm shows `landed < expected` (silent overwrites). Capture these numbers for the report — this is the empirical proof the fixes close a real race. Confirm `concurrent-append-probe-redrun*` is torn down too.

- [ ] **Step 4: Commit (probe only, not the venv)**
```bash
git add service-aid/probes/concurrent-append/probe.py service-aid/probes/concurrent-append/README.md
git commit -m "probe(concurrent-append): real-AWS N-writer race probe for appendOnVal/addIoSetVal"
```
The README documents the red→green protocol and the measured before/after numbers.

---

## Final: whole-branch review + merge

- [ ] **Step 1: Full local suite**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing.py -q`
Expected: all pass. (Optionally re-run the broader `tests/db/` + `tests/app/test_lambding.py` to confirm no backend regression.)

- [ ] **Step 2: Whole-branch review**

Dispatch a final reviewer over `development..HEAD`: confirm both fixes match the spec (local-increment mechanic, `_APPEND_MAX_RETRY`, `MaxON` guard preserved, dedup-best-effort documented, LMDB untouched), the unit tests genuinely fail pre-fix (mutation/`git stash` check), and the probe's red→green numbers are real.

- [ ] **Step 3: Merge to `development`**

Fast-forward merge to `development` (direct, matching prior keripy backend work), remove the worktree, delete the branch:
```bash
cd /Users/seriouscoderone/code/keripy
git merge --ff-only feat/ddb-concurrent-append
git worktree remove .worktrees/ddb-concurrency
git worktree prune
git branch -d feat/ddb-concurrent-append
```

---

## Self-Review Notes

- **Spec coverage:** Fix 1 (appendOnVal retry) → Task 1; Fix 2 (addIoSetVal conditional + retry) → Task 2; `_APPEND_MAX_RETRY=64` → Task 1 Step 3; unit tests (collision via monkeypatched stale-max, no-overwrite, single-writer regression) → Tasks 1-2; real-AWS probe with red→green → Task 3; out-of-scope items (transactional dedup, per-KEL sn, false-404 responders, LMDB) are not implemented, as specified. Covered.
- **Monkeypatch rationale:** moto's GSI is read-after-write consistent, so a natural collision is impossible in unit tests; monkeypatching the stale-max source (`_query_gsi` / `_get_ioset_raw`) is the deterministic way to drive the conditional-put collision and exercise the retry loop. The probe (Task 3) is what proves the *real* race on real AWS.
- **Type/name consistency:** `_APPEND_MAX_RETRY` (Task 1) reused in Task 2; `onKey`/`suffix`/`_hex`/`MaxON`/`_SK_SINGLE`/`_put_item(condition=...)` all match dynamodbing.py. Verify the exact On/IoSet read-accessor names (`getOnVal`, `getOnItemIter`, `getIoSetItemIter`, `_get_ioset_raw`, `_get_ioset_items`) against the source during implementation and adjust the test/probe calls if a name differs.
