# Witness First-Seen via DynamoDB Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the witness Lambda's `reserved_concurrent_executions=1` single-writer cap with a DynamoDB-native per-`(pre, sn)` conditional first-seen claim enforced inside the fork-only `DynamoDBer`, so keri.host witnesses are horizontally scalable and highly available while preserving KERI's serializable-first-seen-per-`(AID, sn)` invariant.

**Architecture:** A new `DynamoDBer.claimFirstSeen` does one conditional `PutItem` (`attribute_not_exists(PK)`) on a dedicated `fseen.` marker store, returning `(won, existing_said)`. `Kever.logEvent` calls it — **capability-guarded** (`getattr(self.db, "claimFirstSeen", None)`) so the LMDB `Baser` path is byte-identical and only the serverless `DynamoDBer` is gated. A different-`said` conflict raises keripy's existing `LikelyDuplicitousError`, routed by a `try/except` wrapper in `Kevery.processEvent` into keripy's existing `escrowLDEvent` (`ldes`). Validated superseding recovery routes to a distinct `supersedeFirstSeen` (conditional replace) via an `is_supersede` flag `Kever.update` already has the inputs to compute. Reads stay unguarded; the CDK witness stack drops the concurrency cap.

**Tech Stack:** Python 3.14, boto3 (DynamoDB resource + low-level client), keripy core (`eventing.py`, `dynamodbing.py`), aws-cdk-lib (witness stack), pytest + moto (unit), multiprocessing (real-AWS probe).

## Global Constraints

- **Repo/branch:** keripy fork `~/code/keripy`, branch `feat/witness-ddb-first-seen` (off `development`). Spec: `docs/superpowers/specs/2026-06-19-witness-ddb-first-seen-concurrency-design.md` (commit `2e7fe9f0`).
- **Push to `fork` remote only** (seriouscoderone) — **never** `origin`/`WebOfTrust`.
- **Test env:** per-worktree venv; install with `pip install -e . aws-cdk-lib constructs boto3 pytest pytest-asyncio moto`. **`moto` is REQUIRED** for `DynamoDBer` tests — without it the `dber` fixture **skips silently** (you will see green counts that omit the real coverage; always confirm the test names actually ran). **Do NOT pass `--import-mode=importlib`** — that is a locksmith-only shadow workaround and is wrong here.
- **Worktree caveat:** this touches keripy *core* (`eventing.py`) + the fork-only `dynamodbing.py`. If executing in a git worktree, create a per-worktree venv and `pip install -e .` in it so edits to `src/keri/**` are the ones under test; otherwise execute on the `feat/witness-ddb-first-seen` checkout directly.
- **Commit footer (keripy convention, NOT the locksmith variant):** end each commit message with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **No upstream divergence in spirit:** new behavior is confined to the fork-only `dynamodbing.py`/`lambding.py`/`keri_cdk/` plus a routing-only, capability-guarded delta in the already-forked `eventing.py`. The LMDB `Baser` path MUST stay behaviorally unchanged (the full `tests/core/` regression must stay green).
- **The `fseen.` store is PER-WITNESS, never shared:** add it to `BASER_STORES` but **NOT** to `SHARED_KEL_STORES` (pooling first-seen across witnesses would defeat each witness independently owning its first-seen / `Receiptor` toad convergence).
- **KERI invariant preserved:** serializable first-seen per `(AID, sn)`. No path may accept two conflicting events or lose a first-seen; the worst realistic case is a converging retry.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/keri/db/dynamodbing.py` | Fork-only DynamoDB backend | **Add** `_FSEEN` const, `_existing_said_from_error` helper, `claimFirstSeen`, `supersedeFirstSeen` |
| `src/keri/app/lambding.py` | Serverless store registry | **Add** `"fseen."` to `BASER_STORES` (not `SHARED_KEL_STORES`) |
| `src/keri/core/eventing.py` | Core event processing (already a fork delta) | **Modify** `Kever.logEvent` (gate + `supersede` param), `Kever.update` (compute/pass `is_supersede`), `Kevery.processEvent` (escrow wrappers) |
| `keri_cdk/witness_stack.py` | Witness Lambda CDK stack | **Remove** `reserved_concurrent_executions=1` (line 94) |
| `tests/db/test_dynamodbing.py` | DynamoDBer unit tests (moto) | **Add** `"fseen."` to `STORES`; claim/supersede tests incl. the ALL_OLD-parse test |
| `tests/db/test_dynamodbing_namespace.py` | Shared-store guard | **Add** assertion `"fseen."` ∈ `BASER_STORES`, ∉ `SHARED_KEL_STORES` |
| `tests/core/test_eventing_firstseen_dynamo.py` (new) | Gate routing end-to-end over moto DynamoDBer | **Create** |
| `tests/cdk/test_witness_stack.py` (existing or new) | CDK synth assertion | **Add** assert witness fn has no reserved concurrency |
| `keri_cdk/probes/first-seen/probe.py` + `README.md` (new) | Real-AWS N-writer first-seen probe | **Create** |
| `CLAUDE.md` (keripy root) | Fork conventions | **Update** dynamodbing section: first-seen store + concurrency model |

---

## Task 1: `DynamoDBer.claimFirstSeen` — the conditional first-seen claim

**Files:**
- Modify: `src/keri/db/dynamodbing.py` (add module imports/const/helper near top; add method on `DynamoDBer`)
- Test: `tests/db/test_dynamodbing.py`

**Interfaces:**
- Produces: `DynamoDBer.claimFirstSeen(self, pre: bytes, sn: int, said: bytes) -> tuple[bool, bytes | None]`
  - `(True, None)` — `said` won the `(pre, sn)` first-seen slot.
  - `(False, existing_said: bytes)` — slot already held by `existing_said`. `existing_said == said` ⇒ idempotent re-delivery; `!= said` ⇒ likely duplicitous.
- Produces: module constant `_FSEEN = "fseen."`; helper `_existing_said_from_error(err) -> bytes | None`.
- Consumes (existing, verified): `onKey(top, on, *, sep=b'.')` (`dynamodbing.py:29`), `_SK_SINGLE = "V"` (`:117`), `self._pk(db, key)`, `self._gsi_pk(db)`, `self._table` (boto3 resource), `self.env.open_db(name)`, `self.getVal(db, key)`, `_hex(key)`, `from botocore.exceptions import ClientError` (already imported).

- [ ] **Step 1: Add `"fseen."` to the test `STORES` list**

In `tests/db/test_dynamodbing.py:35`, change:
```python
STORES = ["evts.", "fels.", "kels.", "sigs.", "test."]
```
to:
```python
STORES = ["evts.", "fels.", "kels.", "sigs.", "test.", "fseen."]
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/db/test_dynamodbing.py` (inside the existing test class that uses the `dber` fixture, matching its indentation):
```python
    def test_claimFirstSeen_first_wins(self, dber):
        """A fresh (pre, sn) slot is claimed; returns (True, None)."""
        won, existing = dber.claimFirstSeen(b"EpreAAA", 1, b"EsaidAAA")
        assert won is True
        assert existing is None

    def test_claimFirstSeen_same_said_idempotent(self, dber):
        """Re-claiming the same slot with the same said is idempotent loss."""
        assert dber.claimFirstSeen(b"EpreAAA", 1, b"EsaidAAA") == (True, None)
        won, existing = dber.claimFirstSeen(b"EpreAAA", 1, b"EsaidAAA")
        assert won is False
        assert existing == b"EsaidAAA"

    def test_claimFirstSeen_different_said_duplicity(self, dber):
        """A different said at the same slot loses and surfaces the winner."""
        assert dber.claimFirstSeen(b"EpreAAA", 1, b"EsaidAAA") == (True, None)
        won, existing = dber.claimFirstSeen(b"EpreAAA", 1, b"EsaidBBB")
        assert won is False
        assert existing == b"EsaidAAA"  # the FIRST said wins; never the later one

    def test_claimFirstSeen_distinct_slots_no_contention(self, dber):
        """Different pre or sn are distinct slots — all win."""
        assert dber.claimFirstSeen(b"EpreAAA", 1, b"Ex")[0] is True
        assert dber.claimFirstSeen(b"EpreAAA", 2, b"Ey")[0] is True   # same pre, next sn
        assert dber.claimFirstSeen(b"EpreBBB", 1, b"Ez")[0] is True   # different pre, same sn

    def test_claimFirstSeen_existing_said_parse_via_fallback(self, dber, monkeypatch):
        """If the SDK/mock omits the ALL_OLD Item on a conditional failure,
        claimFirstSeen still returns the winner via the strong point-read fallback
        (Risk 1: ALL_OLD parse must never silently return None)."""
        from keri.db import dynamodbing as ddb
        assert dber.claimFirstSeen(b"EpreAAA", 1, b"EsaidAAA") == (True, None)
        # Force the ALL_OLD parse to yield nothing, exercising the getVal fallback.
        monkeypatch.setattr(ddb, "_existing_said_from_error", lambda err: None)
        won, existing = dber.claimFirstSeen(b"EpreAAA", 1, b"EsaidBBB")
        assert won is False
        assert existing == b"EsaidAAA"

    def test_existing_said_from_error_parses_raw_binary(self):
        """The ALL_OLD error payload is raw DynamoDB-typed; the helper deserializes
        the Binary `val` to plain bytes."""
        from keri.db.dynamodbing import _existing_said_from_error
        err = type("E", (), {})()
        err.response = {"Item": {"val": {"B": b"EsaidAAA"}}}
        assert _existing_said_from_error(err) == b"EsaidAAA"
        # Missing Item -> None (drives the fallback)
        err2 = type("E", (), {})()
        err2.response = {}
        assert _existing_said_from_error(err2) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/db/test_dynamodbing.py -k claimFirstSeen -v`
Expected: FAIL — `AttributeError: 'DynamoDBer' object has no attribute 'claimFirstSeen'` (and the `_existing_said_from_error` import error).

- [ ] **Step 4: Add the import, constant, and helper near the top of `dynamodbing.py`**

After the existing `_SK_SINGLE = "V"` definition (`:117`) add:
```python
_FSEEN = "fseen."          # first-seen marker store: one row per (pre, sn) holding the
                           # winning said; the per-witness DynamoDB-native single-writer
                           # gate that replaces the witness Lambda reserved_concurrency=1.
```
Add to the imports block (with the other `boto3`/`botocore` imports):
```python
from boto3.dynamodb.types import TypeDeserializer
```
After the imports, add the module-level helper:
```python
_DESER = TypeDeserializer()


def _existing_said_from_error(err):
    """Extract the conflicting said from a ConditionalCheckFailed ALL_OLD payload.

    The error Item is raw DynamoDB-typed ({"val": {"B": b"..."}}) — the resource
    does not deserialize error payloads — so deserialize defensively. Returns None
    when the SDK/mock did not include the Item (caller then falls back to a strong
    point read), so a silent null never misclassifies a replay as duplicity.
    """
    item = getattr(err, "response", {}).get("Item")
    if not item or "val" not in item:
        return None
    val = item["val"]
    try:
        val = _DESER.deserialize(val)
    except (TypeError, AttributeError):
        pass  # some SDK/mock variants hand back an already-deserialized value
    if hasattr(val, "value"):   # boto3 Binary -> bytes
        val = val.value
    return bytes(val) if val is not None else None
```

- [ ] **Step 5: Implement `claimFirstSeen` on `DynamoDBer`**

Add as a method on the `DynamoDBer` class (place it near `putVal`, `:625`):
```python
    def claimFirstSeen(self, pre: bytes, sn: int, said: bytes) -> tuple[bool, bytes | None]:
        """Atomically claim the (pre, sn) first-seen slot for `said`.

        One conditional PutItem (attribute_not_exists(PK)) on the fseen. store,
        strongly consistent by construction (base-table write on the exact PK).
        This closes the TOCTOU window that the eventually-consistent
        db.kels.getLast duplicity check (Kevery.processEvent) can miss under GSI
        lag when concurrent Lambda instances race the same slot.

        Returns:
            (True, None)            -> `said` won; this is the first-seen event.
            (False, existing_said)  -> slot already held. existing_said == said is an
                                       idempotent re-delivery; != said is duplicity.
        """
        fsdb = self.env.open_db(_FSEEN)
        key = onKey(pre, sn)
        item = {
            "PK": self._pk(fsdb, key),
            "SK": _SK_SINGLE,
            "val": said,
            _GSI_PK: self._gsi_pk(fsdb),
            _GSI_SK: _hex(key),
        }
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(PK)",
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
            return True, None
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # Slot already claimed. Prefer the ALL_OLD payload (no extra round trip);
            # fall back to a strongly-consistent point read if absent/unparseable.
            existing = _existing_said_from_error(e)
            if existing is None:
                existing = self.getVal(fsdb, key)
            return False, existing
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/db/test_dynamodbing.py -k "claimFirstSeen or existing_said" -v`
Expected: PASS (6 tests). Confirm they actually ran (not skipped) — if you see "skipped", moto is not installed; `pip install moto` and re-run.

- [ ] **Step 7: Commit**

```bash
git add src/keri/db/dynamodbing.py tests/db/test_dynamodbing.py
git commit -m "feat(dynamodbing): claimFirstSeen conditional first-seen gate

One conditional PutItem (attribute_not_exists) on a dedicated fseen. store,
returning (won, existing_said). ALL_OLD parse with a strong point-read
fallback so the SDK-fragile conflict payload never misclassifies a replay
as duplicity. The per-(pre,sn) DynamoDB-native single-writer gate.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `DynamoDBer.supersedeFirstSeen` — validated recovery replace

**Files:**
- Modify: `src/keri/db/dynamodbing.py`
- Test: `tests/db/test_dynamodbing.py`

**Interfaces:**
- Produces: `DynamoDBer.supersedeFirstSeen(self, pre: bytes, sn: int, said: bytes) -> bool`
  - Conditional replace (`attribute_exists(PK)`) of the first-seen marker for a Kevery-validated superseding recovery. `True` = replaced; `False` = no marker to supersede (slot empty — should not occur post-validation).
- Consumes: same helpers as Task 1.

- [ ] **Step 1: Write the failing tests**

Append to the same test class in `tests/db/test_dynamodbing.py`:
```python
    def test_supersedeFirstSeen_replaces_existing(self, dber):
        """Recovery replaces an existing first-seen marker; the new said reads back."""
        assert dber.claimFirstSeen(b"EpreAAA", 1, b"EixnSAID") == (True, None)
        assert dber.supersedeFirstSeen(b"EpreAAA", 1, b"ErotSAID") is True
        # A subsequent claim now loses to the SUPERSEDING said (slot occupied by it).
        won, existing = dber.claimFirstSeen(b"EpreAAA", 1, b"Eother")
        assert won is False
        assert existing == b"ErotSAID"

    def test_supersedeFirstSeen_no_marker_returns_false(self, dber):
        """Superseding an empty slot returns False (defensive; should not happen
        after Kevery validation)."""
        assert dber.supersedeFirstSeen(b"EpreAAA", 9, b"ErotSAID") is False

    def test_supersedeFirstSeen_same_said_converges(self, dber):
        """Two validated recoveries with the same said converge idempotently."""
        assert dber.claimFirstSeen(b"EpreAAA", 1, b"EixnSAID") == (True, None)
        assert dber.supersedeFirstSeen(b"EpreAAA", 1, b"ErotSAID") is True
        assert dber.supersedeFirstSeen(b"EpreAAA", 1, b"ErotSAID") is True
        assert dber.getVal(dber.env.open_db("fseen."), __import__("keri.db.dynamodbing",
                           fromlist=["onKey"]).onKey(b"EpreAAA", 1)) == b"ErotSAID"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/db/test_dynamodbing.py -k supersedeFirstSeen -v`
Expected: FAIL — `AttributeError: ... has no attribute 'supersedeFirstSeen'`.

- [ ] **Step 3: Implement `supersedeFirstSeen`**

Add directly below `claimFirstSeen` in `dynamodbing.py`:
```python
    def supersedeFirstSeen(self, pre: bytes, sn: int, said: bytes) -> bool:
        """Replace the (pre, sn) first-seen marker for a VALIDATED superseding
        recovery (Kevery's Rules A/B/C already decided this rot/drt supersedes the
        event at sn). Unlike claimFirstSeen this overwrites the immutable marker,
        but conditional on the slot already EXISTING — a recovery cannot create a
        slot ahead of the original first-seen.

        Two distinct *valid* recoveries at one sn would be controller-side
        duplicity (the controller signing two rotations at the same sn) and are out
        of this layer's remit; the same valid recovery arriving concurrently
        converges (idempotent overwrite of the same said).

        Returns True when the marker was replaced, False when no marker existed.
        """
        fsdb = self.env.open_db(_FSEEN)
        key = onKey(pre, sn)
        item = {
            "PK": self._pk(fsdb, key),
            "SK": _SK_SINGLE,
            "val": said,
            _GSI_PK: self._gsi_pk(fsdb),
            _GSI_SK: _hex(key),
        }
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_exists(PK)")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/db/test_dynamodbing.py -k supersedeFirstSeen -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/keri/db/dynamodbing.py tests/db/test_dynamodbing.py
git commit -m "feat(dynamodbing): supersedeFirstSeen validated-recovery replace

Conditional replace (attribute_exists) of the first-seen marker, called only
on a Kevery-validated superseding recovery. Distinct from claimFirstSeen so
the storage layer never confuses validated recovery with concurrent duplicity.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Register the `fseen.` store (and prove it is NOT shared)

**Files:**
- Modify: `src/keri/app/lambding.py:34` (the `BASER_STORES` list)
- Test: `tests/db/test_dynamodbing_namespace.py`

**Interfaces:**
- Consumes: `BASER_STORES` (`lambding.py:34`), `SHARED_KEL_STORES` (same module).
- Produces: `"fseen."` registered as a per-service store so `DynamoEnv.open_db("fseen.")` does not raise `KeyError: Store not configured`.

- [ ] **Step 1: Write the failing guard test**

Append to `tests/db/test_dynamodbing_namespace.py`:
```python
def test_fseen_store_registered_and_not_shared():
    """The first-seen gate store must be a configured per-service store, and must
    NEVER be pooled into the shared key-state oracle (each witness owns its own
    first-seen, like its wigs)."""
    from keri.app.lambding import BASER_STORES, SHARED_KEL_STORES
    assert "fseen." in BASER_STORES
    assert "fseen." not in SHARED_KEL_STORES
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/db/test_dynamodbing_namespace.py -k fseen -v`
Expected: FAIL — `assert 'fseen.' in BASER_STORES`.

- [ ] **Step 3: Add `"fseen."` to `BASER_STORES`**

In `src/keri/app/lambding.py`, add `"fseen."` to the `BASER_STORES` list (begins line 34). Place it with a comment, e.g. after the event-log stores:
```python
    "fseen.",   # per-(pre,sn) first-seen claim marker — the DynamoDB-native
                # single-writer gate (replaces witness reserved_concurrency=1).
                # PER-WITNESS: never add to SHARED_KEL_STORES.
```
Do **not** touch `SHARED_KEL_STORES`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/db/test_dynamodbing_namespace.py -k fseen -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/keri/app/lambding.py tests/db/test_dynamodbing_namespace.py
git commit -m "feat(lambding): register per-witness fseen. first-seen store

Add fseen. to BASER_STORES (configured per-service store) but explicitly NOT to
SHARED_KEL_STORES — each witness owns its first-seen, like its wigs. Guard test.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `eventing.py` gate routing — `Kever.logEvent` + `Kever.update`

**Files:**
- Modify: `src/keri/core/eventing.py` — `Kever.logEvent` (`:3484`), `Kever.update` rot/drt path (`:2376`)
- Test: `tests/core/test_eventing_firstseen_dynamo.py` (new)

**Interfaces:**
- Modifies: `Kever.logEvent(self, serder, sigers=None, wigers=None, wits=None, first=False, delnum=None, diger=None, firner=None, dater=None, local=True, supersede=False)` — adds the trailing `supersede=False` keyword.
- Consumes: `self.db.claimFirstSeen` / `self.db.supersedeFirstSeen` (Tasks 1–2; present only on the DynamoDBer), `LikelyDuplicitousError` (already imported in `eventing.py`).
- Produces (behavior): when `self.db` has no `claimFirstSeen` (LMDB `Baser`), `logEvent` is byte-identical to today. When it does: win → normal first-seen; same-said → idempotent (no second `fn`); different-said → raise `LikelyDuplicitousError`; `supersede=True` → `supersedeFirstSeen` then normal first-seen (new `fn`).

- [ ] **Step 1: Write the failing end-to-end routing tests (over a moto DynamoDBer)**

Create `tests/core/test_eventing_firstseen_dynamo.py`. The event stream (incept → two conflicting ixn at the same sn → a superseding rot) should be built by **cloning keripy's existing duplicity test** for exact helper signatures — first run `grep -n "LikelyDuplicitous\|escrowLDEvent\|def test.*duplicit" tests/core/test_eventing.py` and adapt that test's event construction. Wire it onto a moto-backed `DynamoDBer` Habery so the *gate* (not the in-memory `getLast`) is what catches the conflict:

```python
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri.app import habbing
from keri.core import eventing, coring
from keri.kering import LikelyDuplicitousError


@pytest.fixture
def dynamo_hby():
    """A Habery whose db is a moto-backed DynamoDBer (so claimFirstSeen is live)."""
    if not HAS_MOTO:
        pytest.skip("requires moto")
    from keri.db.dynamodbing import DynamoDBer
    from keri.app.lambding import BASER_STORES
    with mock_aws():
        db = DynamoDBer.open(name="wit", stores=BASER_STORES, region="us-east-1")
        hby = habbing.Habery(name="wit", temp=False, free=True, db=db)
        yield hby
        hby.close()
        db.close(clear=True)


def _fseen(db, pre, sn):
    from keri.db.dynamodbing import onKey
    return db.getVal(db.env.open_db("fseen."), onKey(pre, sn))


def test_firstseen_win_assigns_fn_and_marks_slot(dynamo_hby):
    """An in-order first event is accepted, assigned an fn, and claims the slot."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    pre = hab.pre.encode()
    # icp (sn=0) was first-seen during makeHab:
    assert _fseen(dynamo_hby.db, pre, 0) == hab.kever.serder.saidb


def test_concurrent_different_said_is_duplicity(dynamo_hby):
    """Two different events at the same sn: first wins, second raises
    LikelyDuplicitousError via the gate (mirroring detected duplicity)."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    # Build TWO different ixn at sn=1 (different anchored data => different said).
    # (Clone tests/core/test_eventing.py duplicity test for exact serder/sig build.)
    serderA, sigersA = _make_ixn(hab, sn=1, data=[{"d": "A"}])
    serderB, sigersB = _make_ixn(hab, sn=1, data=[{"d": "B"}])
    kvy = eventing.Kevery(db=dynamo_hby.db, lax=False, local=True)
    _process(kvy, serderA, sigersA)              # A wins
    assert _fseen(dynamo_hby.db, hab.pre.encode(), 1) == serderA.saidb
    with pytest.raises(LikelyDuplicitousError):
        _process(kvy, serderB, sigersB)          # B is duplicity
    # B is escrowed to ldes (proven by Task 5's processEvent wrapper test); the
    # marker still holds A:
    assert _fseen(dynamo_hby.db, hab.pre.encode(), 1) == serderA.saidb


def test_same_said_redelivery_idempotent(dynamo_hby):
    """Re-delivering the exact same event assigns no second fn."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    serderA, sigersA = _make_ixn(hab, sn=1, data=[{"d": "A"}])
    kvy = eventing.Kevery(db=dynamo_hby.db, lax=False, local=True)
    _process(kvy, serderA, sigersA)
    fn1 = hab.kever.fner.num
    _process(kvy, serderA, sigersA)              # idempotent re-delivery
    assert hab.kever.fner.num == fn1             # no second fn assigned


def test_recovery_supersedes_marker(dynamo_hby):
    """A validated superseding rot at the same sn replaces the marker via
    supersedeFirstSeen and gets its own (new) fn."""
    # Build: icp, ixn@1, then a rot@1 that supersedes the ixn (clone the existing
    # superseding-recovery test in tests/core/test_eventing.py for exact build).
    ...  # event construction adapted from the existing recovery test
```

> Helper bodies `_make_ixn` / `_process` and the recovery test body are filled by cloning the corresponding constructions in `tests/core/test_eventing.py` (duplicity + superseding-recovery tests). The assertions above are the deliverable contract; keep them verbatim.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/core/test_eventing_firstseen_dynamo.py -v`
Expected: FAIL — the gate is not wired yet, so `_fseen(...)` is empty for the icp (claimFirstSeen never called) and the different-said case does not raise from the accept path.

- [ ] **Step 3: Add the gate to `Kever.logEvent`**

Add `supersede=False` to the `logEvent` signature (`:3484`). Then split the existing `if first:` block so the gate runs first. Replace:
```python
        pre = self.prefixer.qb64
        if first:  # append event dig to first seen database in order
            fn = self.db.fels.append(keys=serder.preb, val=serder.saidb)
```
with:
```python
        pre = self.prefixer.qb64
        if first:
            # Serverless first-seen gate (DynamoDBer only). The LMDB Baser is
            # single-writer by construction and exposes no claimFirstSeen, so this
            # is a no-op there and the upstream path is unchanged. Closes the TOCTOU
            # that the eventually-consistent db.kels.getLast duplicity check
            # (Kevery) can miss under GSI lag when concurrent Lambdas race the slot.
            claim = getattr(self.db, "claimFirstSeen", None)
            if claim is not None:
                if supersede:  # Kevery-validated superseding recovery (Rules A/B/C)
                    self.db.supersedeFirstSeen(serder.preb, serder.sn, serder.saidb)
                else:
                    won, existing = claim(serder.preb, serder.sn, serder.saidb)
                    if not won:
                        if existing == serder.saidb:
                            first = False  # same event won: idempotent, skip fn
                        else:
                            raise LikelyDuplicitousError(
                                f"Likely Duplicitous Event sn={serder.sn} "
                                f"type={serder.ilk} SAID={serder.said}")
        if first:  # append event dig to first seen database in order
            fn = self.db.fels.append(keys=serder.preb, val=serder.saidb)
```
(Everything from `fn = self.db.fels.append(...)` onward — the `firner`/`dater`/`fons.pin`/logging — stays inside this second `if first:` block unchanged. `kels.add` and `return (fn, ...)` stay after it, unchanged. `fn` is already initialized to `None` at the top of `logEvent`, so the same-said path returns `(None, dts)` and callers' `if fn is not None` correctly skip the state pin.)

- [ ] **Step 4: Pass `is_supersede` from `Kever.update` (rot/drt path)**

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
The ixn branch (`:2442`) is left unchanged — an ixn is never a supersede, and `supersede` defaults to `False`.

- [ ] **Step 5: Run the routing tests**

Run: `pytest tests/core/test_eventing_firstseen_dynamo.py -v`
Expected: the win / same-said / recovery tests PASS. `test_concurrent_different_said_is_duplicity` raises `LikelyDuplicitousError` (PASS on the raise); its `ldes`-escrow assertion is completed by Task 5.

- [ ] **Step 6: Run the LMDB regression to prove no behavior change**

Run: `pytest tests/core/test_eventing.py -q`
Expected: PASS (unchanged) — the gate is a no-op on the LMDB `Baser` (no `claimFirstSeen`).

- [ ] **Step 7: Commit**

```bash
git add src/keri/core/eventing.py tests/core/test_eventing_firstseen_dynamo.py
git commit -m "feat(eventing): route Kever.logEvent through the DynamoDBer first-seen gate

Capability-guarded (getattr) so LMDB Baser is byte-identical. Win -> normal
first-seen; same-said -> idempotent (no second fn); different-said -> raise
existing LikelyDuplicitousError; supersede flag (from update's sn comparison)
-> supersedeFirstSeen. No new decision logic, pure routing.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `Kevery.processEvent` escrow wrappers for the gate's duplicity raise

**Files:**
- Modify: `src/keri/core/eventing.py` — `Kevery.processEvent` acceptance branch (`:4267`) and the fresh-inception `Kever(...)` construction
- Test: `tests/core/test_eventing_firstseen_dynamo.py`

**Interfaces:**
- Consumes: `self.escrowLDEvent(serder=..., sigers=...)` (`:5595`), `LikelyDuplicitousError`.
- Produces (behavior): a `LikelyDuplicitousError` raised by the gate inside `update()`/inception is escrowed to `ldes` and re-raised — byte-for-byte the outcome of the existing duplicity branches (`:4238-4243`, `:4323-4328`).

- [ ] **Step 1: Add the `ldes` assertion to the duplicity test**

Extend `test_concurrent_different_said_is_duplicity` in `tests/core/test_eventing_firstseen_dynamo.py` after the `pytest.raises` block:
```python
    # The loser is escrowed as evidence in ldes (mirrors detected duplicity).
    ldes = list(dynamo_hby.db.ldes.getOnIter(keys=hab.pre.encode(), on=1))
    assert serderB.saidb in [v for v in ldes] or serderB.said in [
        bytes(v).decode() for v in ldes]
```
> Confirm the exact `ldes` accessor by checking how `escrowLDEvent` reads/writes it (`grep -n "ldes" src/keri/core/eventing.py src/keri/db/basing.py`); use the same accessor as the existing duplicity test. The contract is: `serderB` is present in `ldes` at `(pre, sn=1)`.

- [ ] **Step 2: Run to verify the assertion fails**

Run: `pytest tests/core/test_eventing_firstseen_dynamo.py::test_concurrent_different_said_is_duplicity -v`
Expected: FAIL on the new `ldes` assertion (the gate raised, but nothing escrowed it yet).

- [ ] **Step 3: Wrap the acceptance-branch `update()` call**

In `Kevery.processEvent`, the in-order/recovery acceptance branch (`:4258-4271`) calls `kever.update(...)`. Wrap that call:
```python
                    try:
                        kever.update(serder=serder, sigers=sigers, wigers=wigers,
                                     delsner=delsner, delsger=delsger,
                                     firner=firner if self.cloned else None,
                                     dater=dater if self.cloned else None,
                                     eager=eager, local=local, check=self.check)
                    except LikelyDuplicitousError:
                        # The DynamoDBer first-seen gate lost the (pre,sn) race to a
                        # different-said event (concurrent Lambda instances). Mirror
                        # the in-order duplicity branch below: escrow to ldes as
                        # evidence and re-raise so callers treat it as detected
                        # duplicity. (No-op for LMDB: the gate never raises there.)
                        self.escrowLDEvent(serder=serder, sigers=sigers)
                        raise
```

- [ ] **Step 4: Wrap the fresh-inception `Kever(...)` construction**

In `Kevery.processEvent`, locate the branch that handles a *new* inception (`pre not in self.kevers`) and constructs `Kever(...)` (the icp/dip create path, just before the `else: # already accepted inception` at `:4211`). Find the `kever = Kever(...)` call there and wrap it the same way:
```python
                try:
                    kever = Kever(serder=serder, sigers=sigers, wigers=wigers,
                                  db=self.db, ...)   # keep the existing arguments verbatim
                except LikelyDuplicitousError:
                    # Concurrent different-said inception lost the sn=0 gate race.
                    self.escrowLDEvent(serder=serder, sigers=sigers)
                    raise
```
> Use the existing `Kever(...)` argument list verbatim — only add the surrounding `try/except`. Confirm the exact construction site with `grep -n "kever = Kever(\|self.kevers\[" src/keri/core/eventing.py`.

- [ ] **Step 5: Run the duplicity test (and the inception-race assertion if added)**

Run: `pytest tests/core/test_eventing_firstseen_dynamo.py -v`
Expected: PASS, including the `ldes` assertion.

- [ ] **Step 6: Full eventing regression (LMDB unchanged)**

Run: `pytest tests/core/test_eventing.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/keri/core/eventing.py tests/core/test_eventing_firstseen_dynamo.py
git commit -m "feat(eventing): escrow the first-seen gate's duplicity raise to ldes

Wrap the processEvent acceptance update() and fresh-inception Kever() calls so a
LikelyDuplicitousError from the DynamoDBer gate lands in ldes and re-raises,
mirroring keripy's existing detected-duplicity branches. No-op for LMDB.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Drop `reserved_concurrent_executions=1` from the witness stack

**Files:**
- Modify: `keri_cdk/witness_stack.py:94` (and the rationale comment at `:81-82`)
- Test: `tests/cdk/test_witness_stack.py` (existing; add a case — or create if absent)

**Interfaces:**
- Produces (behavior): the synthesized witness Lambda has **no** `ReservedConcurrentExecutions`; reads and writes run concurrently, serialized per-identifier by the DynamoDBer gate. Service-AID (`service_aid.py:112`) and mailbox (`mailbox_stack.py`) are unchanged (out of scope / already uncapped).

- [ ] **Step 1: Write the failing synth assertion**

Add to `tests/cdk/test_witness_stack.py` (match the existing synth-test setup in that file — `App()`, instantiate the stack, `Template.from_stack(...)`):
```python
def test_witness_function_has_no_reserved_concurrency():
    """The witness drops reserved_concurrency=1 — the DynamoDBer first-seen gate
    is the per-identifier serialization point, so the witness scales horizontally."""
    from aws_cdk import App
    from aws_cdk.assertions import Template, Match
    from keri_cdk.witness_stack import WitnessStack
    app = App()
    stack = WitnessStack(app, "TestWit", ...)  # use the same ctor args as the file's existing tests
    template = Template.from_stack(stack)
    template.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "Handler": "witness_handler.handler",
        "ReservedConcurrentExecutions": Match.absent(),
    }))
```
> Copy the `WitnessStack(...)` constructor arguments from the existing tests in this file. If `tests/cdk/test_witness_stack.py` does not exist, create it modeled on the nearest existing `tests/cdk/test_*_stack.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/cdk/test_witness_stack.py -k reserved_concurrency -v`
Expected: FAIL — the template still has `ReservedConcurrentExecutions: 1`.

- [ ] **Step 3: Remove the cap**

In `keri_cdk/witness_stack.py`, delete line 94:
```python
    reserved_concurrent_executions=1,    # ← delete this line
```
and replace the rationale comment (`:81-82`) with:
```python
# No reserved_concurrent_executions: the DynamoDBer per-(pre,sn) conditional
# first-seen claim (claimFirstSeen) is the single-writer serialization point, so
# the witness runs many concurrent instances. See
# docs/superpowers/specs/2026-06-19-witness-ddb-first-seen-concurrency-design.md.
```

- [ ] **Step 4: Run to verify it passes + full CDK synth regression**

Run: `pytest tests/cdk/test_witness_stack.py -v && pytest tests/cdk/ -q`
Expected: PASS (the new case + all existing synth tests green).

- [ ] **Step 5: Commit**

```bash
git add keri_cdk/witness_stack.py tests/cdk/test_witness_stack.py
git commit -m "feat(cdk): drop witness reserved_concurrency=1 (DynamoDBer gate replaces it)

The per-(pre,sn) conditional first-seen claim is now the single-writer point, so
the witness Lambda scales horizontally. Synth test asserts no
ReservedConcurrentExecutions. Service-AID/mailbox unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Real-AWS N-writer first-seen probe

**Files:**
- Create: `keri_cdk/probes/first-seen/probe.py`
- Create: `keri_cdk/probes/first-seen/README.md`

**Interfaces:**
- Consumes: `DynamoDBer.open(...)`, `claimFirstSeen`, `supersedeFirstSeen`; `multiprocessing` spawn workers (model on `keri_cdk/probes/concurrent-append/probe.py`).
- Produces: a CLI probe proving, against **real AWS**, that the conditional write (not a process cap) enforces exactly-one-first-seen under genuine concurrency. **This is an operator-run validation, NOT a CI gate** — moto cannot reproduce true concurrent conditional races, so this never runs in CI and its absence from CI must be stated, not silently implied as coverage.

- [ ] **Step 1: Create the probe**

Create `keri_cdk/probes/first-seen/probe.py`, modeled on `concurrent-append/probe.py` (reuse its `mp.get_context("spawn")` storm harness, table create/teardown, and `--region/--workers/--keep/--teardown-only` argparse). Three storms against the SAME `(pre, sn)`:
```python
"""Real-AWS probe: N concurrent writers race the SAME (pre, sn) first-seen slot.

Proves the DynamoDBer conditional claim — not a process-level cap — enforces
exactly-one-first-seen. moto cannot reproduce true concurrent conditional races,
so this MUST run against real AWS (needs credentials + a real DynamoDB table).
"""
import argparse, os, multiprocessing as mp

PROBE_NAME = "fsprobe"
STORE = "fseen."
PRE = b"EprobePREAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
SN = 1


def _distinct_said_worker(table_name, region, q):
    """Each worker claims (PRE, SN) with a UNIQUE said -> exactly one must win."""
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    said = f"Esaid-{os.getpid():020d}".encode()[:44]
    won, existing = db.claimFirstSeen(PRE, SN, said)
    q.put(("distinct", os.getpid(), won, existing))


def _same_said_worker(table_name, region, q):
    """Every worker claims with the SAME said -> all losers must be idempotent."""
    from keri.db.dynamodbing import DynamoDBer
    db = DynamoDBer.open(name=PROBE_NAME, stores=[STORE],
                         table_name=table_name, region=region)
    said = b"Esame-said-shared-across-all-workers-AAAAAAA"[:44]
    won, existing = db.claimFirstSeen(PRE, SN, said)
    q.put(("same", os.getpid(), won, existing))


def _run(target, table_name, region, workers):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=target, args=(table_name, region, q))
             for _ in range(workers)]
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
    # ... create table (copy create_table from concurrent-append/probe.py) ...
    table_name = f"{PROBE_NAME}-{args.suffix}"

    # Storm 1: distinct saids -> exactly one win, the rest see the SAME winner said.
    d = _run(_distinct_said_worker, table_name, args.region, args.workers)
    wins = [r for r in d if r[2] is True]
    winner_said = wins[0][3] if False else None  # winner reports existing=None
    losers = [r for r in d if r[2] is False]
    loser_saids = {r[3] for r in losers}
    storm1_pass = (len(wins) == 1 and len(loser_saids) == 1)

    # Storm 2 (fresh sn=2): same said -> exactly one True, rest (False, same said).
    # ... repeat with SN=2 and _same_said_worker; storm2_pass = exactly one True
    #     and every loser existing == the shared said ...

    overall = storm1_pass  # and storm2_pass and storm3_pass
    if overall:
        print("VERDICT: PASS — exactly one first-seen winner under real concurrency; "
              "every loser observed the single winning said. The conditional claim "
              "(not a process cap) enforces serializable first-seen per (pre, sn).")
    else:
        print(f"VERDICT: FAIL — wins={len(wins)} distinct_loser_saids={loser_saids}. "
              "More than one winner = the first-seen invariant is OPEN.")
    # ... teardown unless --keep (copy from concurrent-append/probe.py) ...


if __name__ == "__main__":
    main()
```
> Fill the `create_table`/teardown and the Storm-2/Storm-3 bodies by copying the corresponding helpers from `concurrent-append/probe.py`. Storm 3 (optional but recommended per spec Risk 3): a concurrent-recovery storm — pre-seed `(PRE, 3)` with `claimFirstSeen`, then N workers `supersedeFirstSeen(PRE, 3, same_rot_said)` → assert all `True` and the marker converges to `same_rot_said`.

- [ ] **Step 2: Create the README**

Create `keri_cdk/probes/first-seen/README.md` documenting: purpose (proves the claim, not the cap, enforces first-seen), that it requires real AWS creds + a real table (NOT CI / NOT moto), the exact invocation, and the PASS/FAIL verdict meaning. Model the structure on `concurrent-append/README.md`. State plainly: **this probe is the only real-concurrency verification; CI covers single-threaded classification only.**

- [ ] **Step 3: Smoke-check the probe parses/imports (no AWS call)**

Run: `python -c "import ast; ast.parse(open('keri_cdk/probes/first-seen/probe.py').read()); print('ok')"`
Expected: `ok`. (Do **not** run the probe itself here — it needs real AWS. Note in the commit that operator execution is a separate, manual validation step.)

- [ ] **Step 4: Commit**

```bash
git add keri_cdk/probes/first-seen/
git commit -m "test(probe): real-AWS N-writer first-seen probe

N workers race one (pre,sn): exactly one claimFirstSeen win, all losers see the
single winning said; same-said storm is idempotent; concurrent-recovery storm
converges. Operator-run (real AWS), NOT a CI gate — moto cannot reproduce true
concurrent conditional races.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Full regression + docs

**Files:**
- Modify: `CLAUDE.md` (keripy repo root) — the dynamodbing/serverless conventions section
- Test: full suites (no new code)

- [ ] **Step 1: Run the full affected regression**

Run:
```bash
pytest tests/db/ tests/core/test_eventing.py tests/core/test_eventing_firstseen_dynamo.py tests/cdk/ tests/app/test_keri_protocol_dynamo.py -q
```
Expected: all PASS. Confirm the DynamoDBer tests are not silently skipped (moto installed). If `tests/app/test_keri_protocol_dynamo.py` exposes a natural seam, add (or confirm) a single-threaded duplicity case there as defense-in-depth.

- [ ] **Step 2: Update keripy CLAUDE.md**

In the keripy `CLAUDE.md` dynamodbing/serverless section, add a short paragraph:
```
- The witness enforces KERI first-seen via a DynamoDB-native per-(pre,sn) conditional
  claim (`DynamoDBer.claimFirstSeen`, store `fseen.`), NOT a Lambda
  `reserved_concurrent_executions=1` cap (removed). `supersedeFirstSeen` handles
  validated superseding recovery. `fseen.` is PER-WITNESS (in BASER_STORES, never in
  SHARED_KEL_STORES). `Kever.logEvent` calls the gate capability-guarded
  (`getattr(self.db, "claimFirstSeen", ...)`) so the LMDB Baser path is unchanged.
  Spec: docs/superpowers/specs/2026-06-19-witness-ddb-first-seen-concurrency-design.md.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(keripy): witness first-seen now DynamoDB-native (fseen. claim)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Operator validation (manual, outside CI) — record the result**

After deploying the witness stack to a real AWS account, run the probe and paste the verdict into the PR / a follow-up note:
```bash
python keri_cdk/probes/first-seen/probe.py --region us-east-1 --workers 12
```
Expected: `VERDICT: PASS`. This is the real-concurrency proof the spec's Definition of Done requires; do not mark the work complete without it.

---

## Self-Review

**1. Spec coverage:**
- Drop `reserved_concurrent_executions=1` → Task 6. ✓
- `claimFirstSeen` (conditional PutItem, ALL_OLD, no `#HEAD`, no transaction) → Task 1. ✓
- `supersedeFirstSeen` (validated replace) → Task 2. ✓
- Minimal `eventing.py` routing (logEvent gate + update is_supersede + processEvent escrow) → Tasks 4–5; grounded in §Implementation grounding (return contract, insertion point, recovery reads). ✓
- Reads unguarded → unchanged (no read paths touched). ✓
- Reuse keripy `fels` for `fn` → Task 4 leaves `fels.append` in place; the gate only precedes it. ✓
- `ALL_OLD` parse + boto3 fragility (Risk 1) → Task 1 `_existing_said_from_error` + getItem fallback + explicit parse test; **refines** the spec (fragility downgraded from correctness-risk to perf-footnote via the fallback). ✓
- BDD/idempotent/duplicity/cross-AID/recovery → Tasks 1,2,4,5 unit + routing tests. ✓
- Real-AWS N-writer probe → Task 7 (incl. concurrent-recovery storm for Risk 3). ✓
- Regression green → Task 8. ✓
- Scope: witnesses only; Service-AID untouched (`service_aid.py:112` left as-is, noted Task 6); mailbox untouched. ✓
- `fseen.` per-witness, not shared → Task 3 guard. ✓

**2. Placeholder scan:** Two intentional "clone the existing test" pointers (Task 4 event construction, Task 5 `ldes` accessor) — these reference exact existing keripy tests for helper signatures rather than risk shipping subtly-wrong event-construction code; the *assertions/contracts* are concrete. The Task 7 storm-2/3 bodies and `create_table` are "copy from concurrent-append/probe.py" — concrete source, deterministic copy. No `TODO`/"add error handling"/"fill in" placeholders.

**3. Type consistency:** `claimFirstSeen -> (bool, bytes|None)` consumed in Task 4 as `won, existing`. `supersedeFirstSeen -> bool`. `logEvent(..., supersede=False)` set in Task 4, passed in Task 4 step 4. `_FSEEN = "fseen."` used in Tasks 1–3 and tests. `onKey(pre, sn)` / `_SK_SINGLE` / `_pk` / `_gsi_pk` / `_hex` match the verified `dynamodbing.py` symbols. Consistent.

## Notable refinement vs spec (flag for reviewer)

Task 1 implements the `ALL_OLD` conflict parse **with a strongly-consistent `getVal` fallback** when the SDK/mock omits the Item. The spec chose `ALL_OLD` explicitly to avoid a second read and listed the parse fragility as Risk 1 (mitigated by a boto3 pin + parse test). The fallback only fires on the rare conflict path, makes the same-said/different-said classification robust on moto and any boto3 version, and turns Risk 1 from a correctness risk into a perf footnote — a strict improvement, but call it out so the spec's "no second read" intent is consciously superseded.
