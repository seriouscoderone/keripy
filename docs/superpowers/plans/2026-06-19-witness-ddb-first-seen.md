# Witness First-Seen via DynamoDB Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the witness Lambda's `reserved_concurrent_executions=1` single-writer cap with a per-`(pre, sn)` conditional first-seen claim, so keri.host witnesses are horizontally scalable while preserving KERI's serializable-first-seen-per-`(AID, sn)` invariant — with the first-seen logic in the KERI layer (`Kever`) and only generic primitives in the storage layer.

**Architecture:** The storage backend (`DynamoDBer`) exposes only generic primitives — `putVal` (conditional insert, `attribute_not_exists`, returns bool), `getVal` (strong point read), `setVal` (overwrite) — plus one generic flag `singleWriter` (default `True`; DynamoDBer sets `False`). `Kever` (KERI layer) composes first-seen: `_claimFirstSeen` does `putVal`; on a lost claim it reads the incumbent with `getVal`; `_supersedeFirstSeen` does `setVal`. `Kever.logEvent` runs the gate at the head of its `if first:` block, **only when `not getattr(self.db, "singleWriter", True)`**, so the LMDB/desktop path is byte-identical. A different-`said` conflict raises keripy's existing `LikelyDuplicitousError`, routed by a `try/except` in `Kevery.processEvent` into keripy's existing `escrowLDEvent`. The CDK witness stack drops the cap.

**Tech Stack:** Python 3.14, boto3 (DynamoDB resource + client), keripy core (`eventing.py`, `dynamodbing.py`, `lambding.py`), aws-cdk-lib (witness stack), pytest + moto (unit), multiprocessing (real-AWS probe).

## Global Constraints

- **Repo/branch:** keripy fork `~/code/keripy`, branch `feat/witness-ddb-first-seen` (off `development`). Spec: `docs/superpowers/specs/2026-06-19-witness-ddb-first-seen-concurrency-design.md`.
- **Push to `fork` remote only** (seriouscoderone) — **never** `origin`/`WebOfTrust`. (No task pushes; commits land on the local branch.)
- **LAYERING — no concept leaks (binding).** The storage layer (`src/keri/db/dynamodbing.py`, `dbing.py`, `basing.py`) gets ONLY generic data-layer verbs (put/get/set/append/conditional-insert/iterate) and generic properties. Any name carrying a protocol concept — `firstSeen`, `claim`, `supersede`, `receipt`, `duplicity`, `witness`, `recovery`, `escrow`, `KEL` — belongs in the KERI layer (`eventing.py`/`Kever`/`Kevery`). The only storage-layer addition in this plan is the generic `singleWriter` flag. (Store *names* like `fseen.` are config in `lambding.py`/`BASER_STORES`, consistent with the existing `kels.`/`fels.`/`wigs.` registry — the storage engine stays generic over store names.) **Reviewers must reject any KERI vocabulary added to the storage layer.**
- **Test env:** per-worktree venv; install with `pip install -e . aws-cdk-lib constructs boto3 pytest pytest-asyncio moto`. **`moto` is REQUIRED** for `DynamoDBer` tests — without it the `dber` fixture **skips silently** (confirm test names actually ran, not "skipped"). **Do NOT pass `--import-mode=importlib`** — locksmith-only, wrong here.
- **Worktree caveat:** touches keripy *core* (`eventing.py`) + fork-only `dynamodbing.py`/`lambding.py`. If executing in a git worktree, create a per-worktree venv and `pip install -e .` so edits to `src/keri/**` are under test; otherwise execute on the `feat/witness-ddb-first-seen` checkout directly.
- **Commit footer (keripy convention, NOT the locksmith variant):** end each commit message with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **Upstream divergence minimal:** new behavior is in the fork-only `dynamodbing.py`/`lambding.py`/`keri_cdk/` plus a routing-only delta in the already-forked `eventing.py`. The LMDB `Baser` path MUST stay behaviorally unchanged: `singleWriter` defaults `True` via `getattr`, so `dbing.py`/`basing.py` are **not touched** and the gate is a no-op there. The full `tests/core/test_eventing.py` regression must stay green.
- **The `fseen.` store is PER-WITNESS, never shared:** add it to `BASER_STORES` but **NOT** to `SHARED_KEL_STORES`.
- **KERI invariant preserved:** serializable first-seen per `(AID, sn)`. No path may accept two conflicting events or lose a first-seen; the worst realistic case is a converging retry.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/keri/db/dynamodbing.py` | Fork-only DynamoDB backend (generic store) | **Add** one line: `singleWriter = False` class attribute. No KERI methods. |
| `src/keri/app/lambding.py` | Serverless store registry | **Add** `"fseen."` to `BASER_STORES` (not `SHARED_KEL_STORES`) |
| `src/keri/core/eventing.py` | Core event processing (already a fork delta) | **Add** `Kever._claimFirstSeen`/`_supersedeFirstSeen`; **modify** `Kever.logEvent` (gate + `supersede` param), `Kever.update` (`is_supersede`), `Kevery.processEvent` (escrow wrappers) |
| `keri_cdk/witness_stack.py` | Witness Lambda CDK stack | **Remove** `reserved_concurrent_executions=1` (line 94) |
| `tests/db/test_dynamodbing.py` | DynamoDBer unit tests (moto) | **Add** `"fseen."` to `STORES`; `singleWriter` flag test |
| `tests/db/test_dynamodbing_namespace.py` | Shared-store guard | **Add** `"fseen."` ∈ `BASER_STORES`, ∉ `SHARED_KEL_STORES` |
| `tests/core/test_eventing_firstseen_dynamo.py` (new) | First-seen gate end-to-end over moto DynamoDBer + LMDB regression | **Create** |
| `tests/cdk/test_witness_stack.py` (existing or new) | CDK synth assertion | **Add** witness fn has no reserved concurrency |
| `keri_cdk/probes/first-seen/probe.py` + `README.md` (new) | Real-AWS N-writer probe (uses generic `putVal`/`getVal`) | **Create** |
| `CLAUDE.md` (keripy root) | Fork conventions | **Update** dynamodbing/serverless section |

---

## Task 1: Storage-layer prep — `singleWriter` flag + register `fseen.` store

**Files:**
- Modify: `src/keri/db/dynamodbing.py` (add `singleWriter = False` class attribute on `DynamoDBer`)
- Modify: `src/keri/app/lambding.py:34` (`BASER_STORES`)
- Test: `tests/db/test_dynamodbing.py`, `tests/db/test_dynamodbing_namespace.py`

**Interfaces:**
- Produces: `DynamoDBer.singleWriter` (class attribute) `== False`. The contract: "this backend does NOT serialize concurrent writers to a key; callers needing single-writer semantics must enforce them." Backends that omit the attribute default to `True` via `getattr(db, "singleWriter", True)` (consumed in Task 2).
- Produces: `"fseen."` registered in `BASER_STORES` so `DynamoEnv.open_db("fseen.")` (Task 2) does not raise `KeyError: Store not configured`. Not in `SHARED_KEL_STORES`.
- Consumes: existing `BASER_STORES` / `SHARED_KEL_STORES` (`lambding.py`).

- [ ] **Step 1: Write the failing tests**

In `tests/db/test_dynamodbing.py:35`, change:
```python
STORES = ["evts.", "fels.", "kels.", "sigs.", "test."]
```
to:
```python
STORES = ["evts.", "fels.", "kels.", "sigs.", "test.", "fseen."]
```
Append to the test class that uses the `dber` fixture:
```python
    def test_dynamodber_is_not_single_writer(self, dber):
        """DynamoDB has many concurrent writers, so the KERI layer must enforce
        first-seen itself. The generic flag advertises this."""
        assert dber.singleWriter is False

    def test_single_writer_defaults_true_for_other_backends(self):
        """Backends that don't set the flag are treated as single-writer (the
        safe default for LMDB), so getattr returns True and the gate is skipped."""
        class FakeLmdb:
            pass
        assert getattr(FakeLmdb(), "singleWriter", True) is True
```
Append to `tests/db/test_dynamodbing_namespace.py`:
```python
def test_fseen_store_registered_and_not_shared():
    """The first-seen marker store is a configured per-service store, and must
    NEVER be pooled into the shared key-state oracle (each witness owns its own
    first-seen, like its wigs)."""
    from keri.app.lambding import BASER_STORES, SHARED_KEL_STORES
    assert "fseen." in BASER_STORES
    assert "fseen." not in SHARED_KEL_STORES
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/db/test_dynamodbing.py -k "single_writer" -v && pytest tests/db/test_dynamodbing_namespace.py -k fseen -v`
Expected: FAIL — `AttributeError: 'DynamoDBer' object has no attribute 'singleWriter'` and `assert 'fseen.' in BASER_STORES`.

- [ ] **Step 3: Add the `singleWriter` flag**

In `src/keri/db/dynamodbing.py`, add a class attribute to `DynamoDBer` (just after the `class DynamoDBer:` line / docstring, before `__init__`):
```python
class DynamoDBer:
    ...
    # Generic concurrency-model flag: DynamoDB allows many concurrent writers to
    # one key, so a caller needing single-writer semantics (KERI first-seen) must
    # enforce them itself. (LMDB-backed stores are single-writer and omit this,
    # defaulting True via getattr.) NOT a KERI concept — a storage property.
    singleWriter = False
```

- [ ] **Step 4: Register the `fseen.` store**

In `src/keri/app/lambding.py`, add `"fseen."` to the `BASER_STORES` list (begins line 34):
```python
    "fseen.",   # per-(pre,sn) first-seen marker store used by the KERI-layer
                # first-seen gate (Kever) on concurrent backends. PER-WITNESS:
                # never add to SHARED_KEL_STORES.
```
Do **not** touch `SHARED_KEL_STORES`.

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/db/test_dynamodbing.py -k "single_writer" -v && pytest tests/db/test_dynamodbing_namespace.py -k fseen -v`
Expected: PASS (confirm moto active / not skipped). Then `pytest tests/db/test_dynamodbing.py -q` once to confirm no regression.

- [ ] **Step 6: Commit**

```bash
git add src/keri/db/dynamodbing.py src/keri/app/lambding.py tests/db/test_dynamodbing.py tests/db/test_dynamodbing_namespace.py
git commit -m "feat(dynamodbing): singleWriter flag + register per-witness fseen. store

Generic concurrency-model flag (DynamoDBer=False, default True elsewhere) so the
KERI layer knows when it must enforce first-seen itself; no KERI concept in the
storage layer. Register fseen. in BASER_STORES (not SHARED_KEL_STORES). Guards.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: KERI-layer first-seen composition — `Kever._claimFirstSeen` + `logEvent` gate

**Files:**
- Modify: `src/keri/core/eventing.py` — add `Kever._claimFirstSeen`/`_supersedeFirstSeen`; modify `Kever.logEvent` (`:3484`) and `Kever.update` rot/drt path (`:2376`)
- Test: `tests/core/test_eventing_firstseen_dynamo.py` (new)

**Interfaces:**
- Produces: `Kever._claimFirstSeen(self, serder) -> tuple[bool, bytes | None]` — `won = self.db.putVal(fsdb, snKey(pre,sn), said)`; if not won, `(False, self.db.getVal(fsdb, key))`. `Kever._supersedeFirstSeen(self, serder) -> None` — `self.db.setVal(fsdb, snKey(pre,sn), said)`. Both open `fsdb = self.db.env.open_db(b"fseen.")`.
- Modifies: `Kever.logEvent(self, serder, sigers=None, wigers=None, wits=None, first=False, delnum=None, diger=None, firner=None, dater=None, local=True, supersede=False)` — adds trailing `supersede=False`.
- Consumes: storage primitives `self.db.putVal`/`getVal`/`setVal`/`env.open_db` (DynamoDBer); `self.db.singleWriter` (Task 1, via `getattr` default True); `snKey` and `LikelyDuplicitousError` (both already imported in `eventing.py`).

- [ ] **Step 1: Confirm `snKey` is imported in `eventing.py`**

Run: `grep -n "snKey" src/keri/core/eventing.py | head`
Expected: `snKey` is already imported and used (escrow keys). If, unexpectedly, it is not imported, add it to the existing `from keri.db.dbing import ...` line. (`snKey(pre: bytes, sn: int)` builds the `(pre, sn)` key.)

- [ ] **Step 2: Write the failing end-to-end routing tests (over a moto DynamoDBer)**

Create `tests/core/test_eventing_firstseen_dynamo.py`. Build the event stream by **cloning keripy's existing duplicity / superseding-recovery tests** for exact helper signatures — first run `grep -n "LikelyDuplicitous\|escrowLDEvent\|def test.*duplicit\|def test.*supersed\|def test.*recover" tests/core/test_eventing.py` and adapt those tests' event construction. Wire it onto a moto-backed `DynamoDBer` Habery so the *gate* (not the in-memory `getLast`) catches the conflict:
```python
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri.app import habbing
from keri.core import eventing
from keri.db.dbing import snKey
from keri.kering import LikelyDuplicitousError


@pytest.fixture
def dynamo_hby():
    """A Habery whose db is a moto-backed DynamoDBer (singleWriter False -> gate active)."""
    if not HAS_MOTO:
        pytest.skip("requires moto")
    from keri.db.dynamodbing import DynamoDBer
    from keri.app.lambding import BASER_STORES
    with mock_aws():
        db = DynamoDBer.open(name="wit", stores=BASER_STORES, region="us-east-1")
        assert db.singleWriter is False
        hby = habbing.Habery(name="wit", temp=False, free=True, db=db)
        yield hby
        hby.close()
        db.close(clear=True)


def _marker(db, pre, sn):
    return db.getVal(db.env.open_db(b"fseen."), snKey(pre, sn))


def test_firstseen_win_marks_slot(dynamo_hby):
    """An accepted first event claims the (pre, sn) marker."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    assert _marker(dynamo_hby.db, hab.pre.encode(), 0) == hab.kever.serder.saidb


def test_concurrent_different_said_is_duplicity(dynamo_hby):
    """Two different events at the same sn: first wins, second raises
    LikelyDuplicitousError via the gate."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    serderA, sigersA = _make_ixn(hab, sn=1, data=[{"d": "A"}])
    serderB, sigersB = _make_ixn(hab, sn=1, data=[{"d": "B"}])
    kvy = eventing.Kevery(db=dynamo_hby.db, lax=False, local=True)
    _process(kvy, serderA, sigersA)              # A wins
    assert _marker(dynamo_hby.db, hab.pre.encode(), 1) == serderA.saidb
    with pytest.raises(LikelyDuplicitousError):
        _process(kvy, serderB, sigersB)          # B is duplicity
    assert _marker(dynamo_hby.db, hab.pre.encode(), 1) == serderA.saidb


def test_same_said_redelivery_idempotent(dynamo_hby):
    """Re-delivering the exact same event assigns no second fn."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    serderA, sigersA = _make_ixn(hab, sn=1, data=[{"d": "A"}])
    kvy = eventing.Kevery(db=dynamo_hby.db, lax=False, local=True)
    _process(kvy, serderA, sigersA)
    fn1 = hab.kever.fner.num
    _process(kvy, serderA, sigersA)
    assert hab.kever.fner.num == fn1


def test_recovery_supersedes_marker(dynamo_hby):
    """A validated superseding rot at the same sn overwrites the marker."""
    ...  # build icp, ixn@1, then a rot@1 that supersedes the ixn; assert the
        # marker becomes the rot's said. Adapt from the existing recovery test.
```
> `_make_ixn` / `_process` helper bodies and the recovery test body are filled by cloning the corresponding constructions in `tests/core/test_eventing.py`. The assertions above are the deliverable contract; keep them verbatim.

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/core/test_eventing_firstseen_dynamo.py -v`
Expected: FAIL — the gate is not wired, so `_marker(...)` is empty for the icp and the different-said case does not raise from the accept path.

- [ ] **Step 4: Add the KERI-layer composition methods to `Kever`**

Add as private methods on the `Kever` class in `eventing.py` (near `logEvent`):
```python
    def _claimFirstSeen(self, serder):
        """KERI first-seen claim for a backend that does NOT serialize concurrent
        writers (self.db.singleWriter is False). Atomic via the storage layer's
        generic conditional insert (putVal); on a lost claim, read the incumbent
        said with the generic strong getVal. Returns (won, existing_said).
        """
        fsdb = self.db.env.open_db(b"fseen.")
        key = snKey(serder.preb, serder.sn)
        if self.db.putVal(fsdb, key, serder.saidb):
            return True, None
        return False, self.db.getVal(fsdb, key)

    def _supersedeFirstSeen(self, serder):
        """Replace the first-seen marker for a Kevery-validated superseding
        recovery (overwrite via the generic setVal).
        """
        fsdb = self.db.env.open_db(b"fseen.")
        self.db.setVal(fsdb, snKey(serder.preb, serder.sn), serder.saidb)
```

- [ ] **Step 5: Add the gate to `Kever.logEvent`**

Add `supersede=False` to the `logEvent` signature (`:3484`). Then split the existing `if first:` block so the gate runs first. Replace:
```python
        pre = self.prefixer.qb64
        if first:  # append event dig to first seen database in order
            fn = self.db.fels.append(keys=serder.preb, val=serder.saidb)
```
with:
```python
        pre = self.prefixer.qb64
        if first and not getattr(self.db, "singleWriter", True):
            # The store does not serialize concurrent writers (the serverless
            # DynamoDB backend across many witness Lambda instances), so enforce
            # KERI first-seen here. LMDB is single-writer by construction
            # (singleWriter absent -> default True) and skips this entirely:
            # byte-identical to upstream. Closes the TOCTOU that the eventually-
            # consistent db.kels.getLast duplicity check (Kevery) can miss under
            # GSI lag when concurrent Lambdas race the slot.
            if supersede:  # Kevery-validated superseding recovery (Rules A/B/C)
                self._supersedeFirstSeen(serder)
            else:
                won, existing = self._claimFirstSeen(serder)
                if not won:
                    if existing == serder.saidb:
                        first = False  # same event won the slot: idempotent, skip fn
                    else:
                        raise LikelyDuplicitousError(
                            f"Likely Duplicitous Event sn={serder.sn} "
                            f"type={serder.ilk} SAID={serder.said}")
        if first:  # append event dig to first seen database in order
            fn = self.db.fels.append(keys=serder.preb, val=serder.saidb)
```
(Everything from `fn = self.db.fels.append(...)` onward — `firner`/`dater`/`fons.pin`/logging — stays inside this second `if first:` block unchanged. `kels.add` and `return (fn, ...)` stay after it. `fn` is initialized to `None` at the top of `logEvent`, so the same-said path returns `(None, dts)` and callers' `if fn is not None` correctly skip the state pin.)

- [ ] **Step 6: Pass `is_supersede` from `Kever.update` (rot/drt path)**

In the rot/drt branch of `Kever.update`, replace the `logEvent` call (`:2376`):
```python
            fn, dts = self.logEvent(serder=serder, sigers=sigers, wigers=wigers,
                                    wits=wits,
                                    first=True if not check else False,
                                    delnum=delsner, diger=delsger,
                                    firner=firner, dater=dater, local=local)
```
with:
```python
            # Superseding recovery if this rot/drt sits at or before the current
            # accepted sn (matches rotate()'s recovery branch sner.num <= self.sner.num).
            # self.sner is still the prior state here (updated below).
            is_supersede = sner.num <= self.sner.num
            fn, dts = self.logEvent(serder=serder, sigers=sigers, wigers=wigers,
                                    wits=wits,
                                    first=True if not check else False,
                                    delnum=delsner, diger=delsger,
                                    firner=firner, dater=dater, local=local,
                                    supersede=is_supersede)
```
The ixn branch (`:2442`) is unchanged — an ixn is never a supersede, and `supersede` defaults `False`.

- [ ] **Step 7: Run the routing tests + the LMDB regression**

Run: `pytest tests/core/test_eventing_firstseen_dynamo.py -v`
Expected: win / same-said / recovery PASS; the duplicity test raises `LikelyDuplicitousError` (PASS on the raise; its `ldes`-escrow assertion is completed in Task 3).
Run: `pytest tests/core/test_eventing.py -q`
Expected: PASS, unchanged — the gate is a no-op on the LMDB `Baser` (`singleWriter` defaults True).

- [ ] **Step 8: Commit**

```bash
git add src/keri/core/eventing.py tests/core/test_eventing_firstseen_dynamo.py
git commit -m "feat(eventing): KERI-layer first-seen gate over generic storage primitives

Kever._claimFirstSeen (putVal + getVal-on-conflict) / _supersedeFirstSeen (setVal),
gated in logEvent by 'not getattr(self.db, \"singleWriter\", True)' so LMDB is
byte-identical. Win -> normal first-seen; same-said -> idempotent; different-said
-> existing LikelyDuplicitousError; supersede flag (from update) -> setVal. The
first-seen concept lives in the KERI layer; the storage layer stays generic.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Fix `escrowLDEvent` (pre-existing bug) + `Kevery.processEvent` escrow wrappers

**Why this task grew (read first):** the gate's different-`said` loser must land in keripy's `ldes` escrow, exactly like keripy's pre-existing detected-duplicity branches. But Task 2 surfaced — and the controller confirmed by runtime introspection — that **`escrowLDEvent` is broken in this fork:** it calls `self.db.addLde(...)`, a method that **does not exist on `Baser` (LMDB) or `DynamoDBer`** (no `def addLde` anywhere in `src/keri/`, no `__getattr__`, not on a live instance). `db.ldes` is now a modern `OnIoDupSuber`; `escrowLDEvent`'s *read/rem* side already uses it (`self.db.ldes.getAllItemIter`/`.rem` at `:7260`/`:7330`/`:7339`) but the **add side was left on the removed `addLde`** — a partial-migration bug, latent because no test exercises `escrowLDEvent`. **Both** pre-existing duplicity branches (`self.escrowLDEvent(...)` at the icp-duplicate and in-order paths) hit it too. Per the user decision (2026-06-20), **fix it properly** (migrate the add-side to `db.ldes.add`), then the gate wrapper and the pre-existing branches all share a working `escrowLDEvent`.

**Files:**
- Modify: `src/keri/core/eventing.py` — `escrowLDEvent` add-side; `unescrowLDEvents` add-side; `Kevery.processEvent` acceptance branch + the fresh-inception `Kever(...)` construction
- Test: `tests/core/test_eventing_firstseen_dynamo.py`

**Interfaces:**
- Consumes: `self.db.ldes.add(keys, on=0, val=...)` — the modern `OnIoDupSuber.add` (`subing.py:2810`); authoritative existing call pattern is `self.db.ldes.rem(keys=pre, on=sn, val=...)` (`eventing.py:7330`). `LikelyDuplicitousError` (already imported).
- Produces (behavior): `escrowLDEvent` actually writes to `ldes` on both backends; a `LikelyDuplicitousError` raised by the gate inside `update()`/inception is escrowed to `ldes` and re-raised — matching keripy's existing duplicity branches. No-op for LMDB (gate never raises there; but the `escrowLDEvent` fix also repairs LMDB's pre-existing duplicity escrow).

- [ ] **Step 0: Confirm the exact call sites (they drifted after Task 2)**

Run: `grep -n "addLde\|self.db.ldes\.\|def escrowLDEvent\|def unescrowLDEvents\|self.escrowLDEvent\|kever = Kever(\|pre not in self.kevers" src/keri/core/eventing.py`
You should see: two `self.db.addLde(...)` add-side calls (in `escrowLDEvent` and `unescrowLDEvents`), the modern `self.db.ldes.getAllItemIter`/`.rem` reads, two `self.escrowLDEvent(...)` pre-existing duplicity call sites, and the fresh-inception `kever = Kever(...)` site. Use the line numbers you find — do not trust stale numbers in this plan.

- [ ] **Step 1: Write the failing tests (the escrow fix + the gate escrow assertion)**

(1) Add a direct escrow-fix test to `tests/core/test_eventing_firstseen_dynamo.py` that proves `escrowLDEvent` writes to `ldes` (this is what RED-confirms the `addLde` bug — it currently raises `AttributeError`):
```python
def test_escrowLDEvent_writes_to_ldes(dynamo_hby):
    """escrowLDEvent must land the event in the ldes store (regression for the
    pre-existing addLde partial-migration bug)."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    serderB, sigersB = _make_ixn(hab, sn=1, data=[{"d": "B"}])
    kvy = eventing.Kevery(db=dynamo_hby.db, lax=False, local=True)
    kvy.escrowLDEvent(serder=serderB, sigers=sigersB)
    escrowed = [bytes(edig) for (_pre,), _sn, edig in
                dynamo_hby.db.ldes.getAllItemIter(keys=hab.pre.encode())]
    assert serderB.saidb in escrowed
```
(2) Extend `test_concurrent_different_said_is_duplicity` after the `pytest.raises` block so the gate's loser is escrowed:
```python
    # The loser is escrowed as evidence in ldes (mirrors detected duplicity).
    escrowed = [bytes(edig) for (_pre,), sn, edig in
                dynamo_hby.db.ldes.getAllItemIter(keys=hab.pre.encode()) if sn == 1]
    assert serderB.saidb in escrowed
```
(3) Strengthen `test_same_said_redelivery_idempotent` (Task 2 review Minor — its `fner` assertion was on the wrong handle and held trivially). Replace its assertion with marker-and-fels checks that are load-bearing regardless of handle:
```python
    # The slot still holds the original said and no second first-seen was logged.
    assert _marker(dynamo_hby.db, hab.pre.encode(), 1) == serderA.saidb
    fels = list(dynamo_hby.db.fels.getIter(keys=hab.pre.encode()))
    assert len([f for f in fels]) == 2  # icp (fn=0) + the single ixn (fn=1); no dup
```
> If `fels.getIter` is not the right accessor, use the one the existing tests use to enumerate the first-seen log (grep `fels` in `tests/core/test_eventing_v1.py`/`v2.py`); the contract is "exactly one first-seen entry for the ixn at sn=1, marker unchanged."

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/core/test_eventing_firstseen_dynamo.py -v`
Expected: `test_escrowLDEvent_writes_to_ldes` FAILS with `AttributeError: 'DynamoDBer' object has no attribute 'addLde'` (the pre-existing bug). The duplicity test fails on its new `ldes` assertion. (The same-said test may already pass on its strengthened marker assertion — that's fine.)

- [ ] **Step 3: Fix the `escrowLDEvent` / `unescrowLDEvents` add-side (the partial-migration bug)**

In `escrowLDEvent`, replace the add-side call:
```python
        self.db.addLde(snKey(serder.preb, serder.sn), serder.saidb)
```
with the modern Suber API (matching the existing `.rem(keys=pre, on=sn, val=...)` pattern):
```python
        self.db.ldes.add(keys=serder.preb, on=serder.sn, val=serder.saidb)
```
In `unescrowLDEvents`, replace:
```python
            self.db.addLde(snKey(pre, sn), serder.digb)
```
with:
```python
            self.db.ldes.add(keys=pre, on=sn, val=serder.digb)
```
Also update the stale docstring line in `unescrowLDEvents` that reads `Uses .db.addLde(self, key, val) which is IOVal with dups.` to reference `self.db.ldes.add(keys, on, val)`. Do not change any other line; the read/rem side already uses the modern API.

- [ ] **Step 4: Wrap the acceptance-branch `update()` call**

In `Kevery.processEvent`, the in-order/recovery acceptance branch calls `kever.update(...)`. Wrap that exact call (use the argument list you see in the file verbatim):
```python
                    try:
                        kever.update(serder=serder, sigers=sigers, wigers=wigers,
                                     delsner=delsner, delsger=delsger,
                                     firner=firner if self.cloned else None,
                                     dater=dater if self.cloned else None,
                                     eager=eager, local=local, check=self.check)
                    except LikelyDuplicitousError:
                        # The first-seen gate lost the (pre,sn) race to a different-
                        # said event (concurrent Lambda instances). Mirror the in-order
                        # duplicity branch: escrow to ldes and re-raise so callers treat
                        # it as detected duplicity. (No-op for LMDB; gate never raises there.)
                        self.escrowLDEvent(serder=serder, sigers=sigers)
                        raise
```

- [ ] **Step 5: Wrap the fresh-inception `Kever(...)` construction**

Locate the *new* inception branch (`pre not in self.kevers`) that constructs `kever = Kever(...)`. Wrap that call (keep its existing arguments verbatim — only add the `try/except`):
```python
                try:
                    kever = Kever(serder=serder, sigers=sigers, wigers=wigers,
                                  db=self.db, ...)   # existing args verbatim
                except LikelyDuplicitousError:
                    # Concurrent different-said inception lost the sn=0 gate race.
                    self.escrowLDEvent(serder=serder, sigers=sigers)
                    raise
```

- [ ] **Step 6: Run the gate/escrow tests**

Run: `pytest tests/core/test_eventing_firstseen_dynamo.py -v`
Expected: all PASS (escrow-fix test, duplicity-with-ldes, win, same-said-strengthened, recovery), moto active not skipped.

- [ ] **Step 7: Full eventing regression (LMDB unchanged + escrow fix doesn't regress)**

Run: `pytest tests/core/test_eventing_v1.py tests/core/test_eventing_v2.py tests/core/test_kevery.py -q`
Expected: PASS. (The `addLde→ldes.add` fix can only repair a path that previously would have crashed; no passing test hit `addLde`, so none regress. The gate is a no-op on LMDB.)

- [ ] **Step 8: Commit**

```bash
git add src/keri/core/eventing.py tests/core/test_eventing_firstseen_dynamo.py
git commit -m "fix(eventing): repair escrowLDEvent + escrow the gate's duplicity raise to ldes

escrowLDEvent/unescrowLDEvents called a removed db.addLde (partial migration bug,
latent — no test exercised it); migrate the add-side to the modern
db.ldes.add(keys,on,val), matching the read/rem side already on it. Then wrap the
processEvent acceptance update() and fresh-inception Kever() so the first-seen
gate's LikelyDuplicitousError lands in ldes and re-raises, like keripy's existing
duplicity branches (now also repaired). No-op for LMDB. Strengthen the same-said
idempotency test (marker + fels) per Task 2 review.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Drop `reserved_concurrent_executions=1` from the witness stack

**Files:**
- Modify: `keri_cdk/witness_stack.py:94` (and the rationale comment at `:81-82`)
- Test: `tests/cdk/test_witness_stack.py` (existing; add a case — or create if absent)

**Interfaces:**
- Produces (behavior): the synthesized witness Lambda has **no** `ReservedConcurrentExecutions`; per-identifier serialization is the KERI-layer first-seen gate. Service-AID (`service_aid.py:112`) and mailbox (`mailbox_stack.py`) are unchanged (out of scope / already uncapped).

- [ ] **Step 1: Write the failing synth assertion**

Add to `tests/cdk/test_witness_stack.py` (match the existing synth-test setup — `App()`, instantiate the stack, `Template.from_stack(...)`):
```python
def test_witness_function_has_no_reserved_concurrency():
    """The witness drops reserved_concurrency=1 — the KERI-layer first-seen gate
    is the per-identifier serialization point, so the witness scales horizontally."""
    from aws_cdk import App
    from aws_cdk.assertions import Template, Match
    from keri_cdk.witness_stack import WitnessStack
    app = App()
    stack = WitnessStack(app, "TestWit", ...)  # same ctor args as the file's existing tests
    template = Template.from_stack(stack)
    template.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "Handler": "witness_handler.handler",
        "ReservedConcurrentExecutions": Match.absent(),
    }))
```
> Copy the `WitnessStack(...)` constructor arguments from the existing tests in this file. If the file doesn't exist, create it modeled on the nearest `tests/cdk/test_*_stack.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/cdk/test_witness_stack.py -k reserved_concurrency -v`
Expected: FAIL — the template still has `ReservedConcurrentExecutions: 1`.

- [ ] **Step 3: Remove the cap**

In `keri_cdk/witness_stack.py`, delete line 94 (`reserved_concurrent_executions=1,`) and replace the rationale comment (`:81-82`) with:
```python
# No reserved_concurrent_executions: the KERI-layer per-(pre,sn) conditional
# first-seen gate (Kever._claimFirstSeen over the DynamoDBer's conditional putVal)
# is the single-writer serialization point, so the witness runs many concurrent
# instances. See docs/superpowers/specs/2026-06-19-witness-ddb-first-seen-concurrency-design.md.
```

- [ ] **Step 4: Run to verify it passes + full CDK synth regression**

Run: `pytest tests/cdk/test_witness_stack.py -v && pytest tests/cdk/ -q`
Expected: PASS (the new case + all existing synth tests green).

- [ ] **Step 5: Commit**

```bash
git add keri_cdk/witness_stack.py tests/cdk/test_witness_stack.py
git commit -m "feat(cdk): drop witness reserved_concurrency=1 (first-seen gate replaces it)

The KERI-layer per-(pre,sn) conditional first-seen gate is now the single-writer
point, so the witness Lambda scales horizontally. Synth test asserts no
ReservedConcurrentExecutions. Service-AID/mailbox unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Real-AWS N-writer first-seen probe

**Files:**
- Create: `keri_cdk/probes/first-seen/probe.py`
- Create: `keri_cdk/probes/first-seen/README.md`

**Interfaces:**
- Consumes: `DynamoDBer.open(...)`, the generic `putVal`/`getVal`, `snKey`; `multiprocessing` spawn workers (model on `keri_cdk/probes/concurrent-append/probe.py`).
- Produces: a CLI probe proving, against **real AWS**, that the conditional `putVal` (not a process cap) enforces exactly-one-first-seen under genuine concurrency. **Operator-run, NOT a CI gate** — moto cannot reproduce true concurrent conditional races; CI covers single-threaded classification only.

- [ ] **Step 1: Create the probe**

Create `keri_cdk/probes/first-seen/probe.py`, modeled on `concurrent-append/probe.py` (reuse its `mp.get_context("spawn")` storm harness, table create/teardown, and `--region/--workers/--keep/--teardown-only` argparse). It hammers the generic conditional insert directly (that IS the gate mechanism):
```python
"""Real-AWS probe: N concurrent writers race the SAME (pre, sn) first-seen slot
via the generic conditional putVal. Proves the conditional write — not a process
cap — enforces exactly-one-first-seen. Needs real AWS (creds + a real table);
moto cannot reproduce true concurrent conditional races. NOT a CI gate."""
import argparse, os, multiprocessing as mp
from keri.db.dbing import snKey

PROBE_NAME = "fsprobe"
STORE = "fseen."
PRE = b"EprobePREAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _distinct_worker(table_name, region, sn, q):
    """Each worker claims (PRE, sn) with a UNIQUE said -> exactly one must win."""
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    fsdb = db.env.open_db(STORE.encode())
    said = f"Esaid-{os.getpid():020d}".encode()[:44]
    won = db.putVal(fsdb, snKey(PRE, sn), said)
    existing = None if won else db.getVal(fsdb, snKey(PRE, sn))
    q.put((os.getpid(), bool(won), bytes(said), existing if existing is None else bytes(existing)))


def _same_worker(table_name, region, sn, said, q):
    """Every worker claims with the SAME said -> exactly one win, rest idempotent."""
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    fsdb = db.env.open_db(STORE.encode())
    won = db.putVal(fsdb, snKey(PRE, sn), said)
    existing = None if won else db.getVal(fsdb, snKey(PRE, sn))
    q.put((os.getpid(), bool(won), existing if existing is None else bytes(existing)))


def _run(target, args_tuple, workers):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=target, args=args_tuple + (q,)) for _ in range(workers)]
    for p in procs: p.start()
    rows = [q.get() for _ in procs]
    for p in procs: p.join()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--suffix", default="run1")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    table_name = f"{PROBE_NAME}-{args.suffix}"
    # ... create table (copy create_table from concurrent-append/probe.py) ...

    # Storm 1: distinct saids @sn=1 -> exactly one win; every loser's existing == winner's said.
    d = _run(_distinct_worker, (table_name, args.region, 1), args.workers)
    wins = [r for r in d if r[1]]
    winner_said = wins[0][2] if len(wins) == 1 else None
    loser_existing = {r[3] for r in d if not r[1]}
    storm1 = (len(wins) == 1 and loser_existing == {winner_said})

    # Storm 2: same said @sn=2 -> exactly one win, every loser existing == that said.
    same_said = b"Esame-said-shared-across-all-workers-AAAAAAA"[:44]
    s = _run(_same_worker, (table_name, args.region, 2, same_said), args.workers)
    storm2 = (len([r for r in s if r[1]]) == 1
              and all(r[2] == same_said for r in s if not r[1]))

    overall = storm1 and storm2
    print("VERDICT: PASS — exactly one first-seen winner under real concurrency; "
          "every loser observed the single winning said. The conditional putVal "
          "(not a process cap) enforces serializable first-seen per (pre, sn)."
          if overall else
          f"VERDICT: FAIL — storm1_wins={len(wins)} loser_existing={loser_existing}. "
          "More than one winner = the first-seen invariant is OPEN.")
    # ... teardown unless --keep (copy from concurrent-append/probe.py) ...


if __name__ == "__main__":
    main()
```
> Fill `create_table`/teardown by copying the helpers from `concurrent-append/probe.py`. Optional Storm 3 (recovery convergence): pre-seed `(PRE, 3)` with a `putVal`, then N workers `db.setVal(fsdb, snKey(PRE,3), rot_said)` → assert the marker converges to `rot_said`.

- [ ] **Step 2: Create the README**

Create `keri_cdk/probes/first-seen/README.md` (model on `concurrent-append/README.md`): purpose (proves the conditional `putVal`, not the cap, enforces first-seen), that it requires real AWS creds + a real table (NOT CI / NOT moto), the exact invocation, and the verdict meaning. State plainly: **this probe is the only real-concurrency verification; CI covers single-threaded classification only.**

- [ ] **Step 3: Smoke-check the probe parses (no AWS call)**

Run: `python -c "import ast; ast.parse(open('keri_cdk/probes/first-seen/probe.py').read()); print('ok')"`
Expected: `ok`. (Do NOT run the probe — it needs real AWS. Operator execution is a separate manual step.)

- [ ] **Step 4: Commit**

```bash
git add keri_cdk/probes/first-seen/
git commit -m "test(probe): real-AWS N-writer first-seen probe (generic putVal)

N workers race one (pre,sn) via the conditional putVal: exactly one win, all
losers observe the single winning said; same-said storm idempotent. Operator-run
(real AWS), NOT a CI gate — moto cannot reproduce concurrent conditional races.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Full regression + docs

**Files:**
- Modify: `CLAUDE.md` (keripy repo root) — the dynamodbing/serverless conventions section
- Test: full suites (no new code)

- [ ] **Step 1: Run the full affected regression**

Run:
```bash
pytest tests/db/ tests/core/test_eventing.py tests/core/test_eventing_firstseen_dynamo.py tests/cdk/ tests/app/test_keri_protocol_dynamo.py -q
```
Expected: all PASS. Confirm the DynamoDBer/moto tests are not silently skipped.

- [ ] **Step 2: Update keripy CLAUDE.md**

In the keripy `CLAUDE.md` dynamodbing/serverless section, add:
```
- The witness enforces KERI first-seen via a per-(pre,sn) conditional claim composed
  in the KERI layer (`Kever._claimFirstSeen`/`_supersedeFirstSeen` over the generic
  `DynamoDBer.putVal`/`getVal`/`setVal`, store `fseen.`), NOT a Lambda
  `reserved_concurrent_executions=1` cap (removed). The storage layer carries NO
  KERI vocabulary — only a generic `singleWriter` flag (DynamoDBer=False, default
  True). The gate is skipped on the single-writer LMDB Baser (byte-identical to
  upstream). `fseen.` is PER-WITNESS (BASER_STORES, never SHARED_KEL_STORES).
  Spec: docs/superpowers/specs/2026-06-19-witness-ddb-first-seen-concurrency-design.md.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(keripy): witness first-seen via KERI-layer gate over generic storage

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Operator validation (manual, outside CI) — record the result**

After deploying the witness stack to a real AWS account, run the probe and paste the verdict into the PR / a follow-up note:
```bash
python keri_cdk/probes/first-seen/probe.py --region us-east-1 --workers 12
```
Expected: `VERDICT: PASS`. This is the real-concurrency proof the Definition of Done requires; do not mark the work complete without it.

---

## Self-Review

**1. Spec coverage:**
- Drop `reserved_concurrent_executions=1` → Task 4. ✓
- Conditional first-seen claim (generic `putVal`, no `#HEAD`, no transaction, no `ALL_OLD`) → Task 2 (`_claimFirstSeen`). ✓
- Supersede via `setVal` → Task 2 (`_supersedeFirstSeen`). ✓
- Storage layer generic only + `singleWriter` flag (no concept leak) → Task 1 + the Global Constraints layering rule. ✓
- KERI-layer composition + `logEvent`/`update`/`processEvent` routing → Tasks 2–3; grounded in §Implementation grounding. ✓
- Reads unguarded → unchanged. ✓
- `fseen.` per-witness, not shared → Task 1 guard. ✓
- LMDB byte-identical (gate no-op via `getattr(...singleWriter, True)`) → Task 2 + regression in Tasks 2,3,6. ✓
- Idempotent/duplicity/cross-AID/recovery → Task 2 tests + Task 3 escrow. ✓
- Real-AWS N-writer probe → Task 5. ✓
- Regression green + docs → Task 6. ✓
- Scope: witnesses only; Service-AID untouched; mailbox untouched; `dbing.py`/LMDB untouched. ✓

**2. Placeholder scan:** Two intentional "clone the existing keripy test" pointers (Task 2 event construction, Task 3 `ldes` accessor) reference exact existing tests for helper signatures; the *assertions/contracts* are concrete. Task 5 `create_table`/teardown are "copy from concurrent-append/probe.py" — concrete source. No `TODO`/"add error handling"/"fill in".

**3. Type consistency:** `_claimFirstSeen -> (bool, bytes|None)` consumed in `logEvent` as `won, existing`. `_supersedeFirstSeen -> None`. `logEvent(..., supersede=False)`; `update` passes `supersede=is_supersede`. Storage verbs `putVal(db,key,val)->bool` / `getVal(db,key)->bytes|None` / `setVal(db,key,val)` and `env.open_db` / `singleWriter` match the verified `dynamodbing.py` surface. `snKey(pre,sn)` is the core key helper. Consistent.
