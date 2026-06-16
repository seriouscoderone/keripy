# Shared-KEL Key-State Oracle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the public KEL/receipt/key-state stores into one shared `shared` namespace on the `keri-core` table (a trust-domain key-state oracle), keeping node-state/escrows/`Reger` per-service — via a backward-compatible per-store routing capability in `DynamoDBer`.

**Architecture:** `DynamoDBer` gains `shared_namespace`/`shared_stores` params; the single key chokepoint `_nskey` resolves namespace per store. `SHARED_KEL_STORES` (a subset of `BASER_STORES`) lives in `src/keri/app/lambding.py`. Witness/mailbox/Service-AID-`db` opens pass the shared args; the Service-AID `Reger` and Mailbox `Mailboxer` stay private. Lambda IAM grants the union of shared + per-service `LeadingKeys` patterns.

**Tech Stack:** Python 3.14, keripy `DynamoDBer`, moto, pytest, aws-cdk-lib assertions. Spec: `docs/superpowers/specs/2026-06-15-cdk-kel-oracle-design.md`.

**Critical-file caution:** `src/keri/db/dynamodbing.py` is the repo's most correctness-sensitive file (Phase A concurrency + the namespacing scheme live there). Keep the change to the `_nskey` chokepoint + the two new params — do not touch other methods.

---

### Task 0: Worktree venv + clean baseline

**Files:** worktree off `development` (create via superpowers:using-git-worktrees, e.g. `~/code/keripy/.worktrees/cdk-oracle` on branch `feat/cdk-kel-oracle`).

- [ ] **Step 1: Create venv + deps**
```bash
cd <worktree>
python3.14 -m venv .venv
.venv/bin/pip install -U pip -q
.venv/bin/pip install -e . -q
.venv/bin/pip install aws-cdk-lib constructs moto boto3 pytest pytest-asyncio -q
```
- [ ] **Step 2: Placeholder layer asset (so CDK synth tests resolve `Code.from_asset`)**
```bash
mkdir -p keri_cdk/layers/keri_runtime/python
echo "placeholder for synth-only tests; real layer built by build_layer.sh" > keri_cdk/layers/keri_runtime/README.txt
```
(`keri_cdk/layers/keri_runtime/` is gitignored — confirm with `git check-ignore keri_cdk/layers/keri_runtime/README.txt`.)
- [ ] **Step 3: Baseline green**
Run: `.venv/bin/python -m pytest tests/cdk tests/handlers tests/db -q`
Expected: PASS (note the count). Proceed only if green.

---

### Task 1: `DynamoDBer` per-store namespace routing

**Files:**
- Modify: `src/keri/db/dynamodbing.py` (`__init__` ~`:196`, `open` ~`:224`, `_nskey` ~`:346`)
- Test: `tests/db/test_dynamodbing_namespace.py`

- [ ] **Step 1: Write failing tests** — append to `tests/db/test_dynamodbing_namespace.py`:
```python
def test_nskey_routes_shared_store_to_shared_namespace():
    """A store in shared_stores routes to shared_namespace; others to the instance namespace."""
    db = _dber(name="svc", namespace="svc:kel",
               shared_namespace="shared", shared_stores={"kels."})
    kels = DynamoSubDb(name="kels.", table_name="core")
    habs = DynamoSubDb(name="habs.", table_name="core")
    assert db._pk(kels, b"AID") == f"shared#kels.#{_hex(b'AID')}"
    assert db._gsi_pk(kels) == "shared#kels."
    assert db._pk(habs, b"AID") == f"svc:kel#habs.#{_hex(b'AID')}"
    assert db._gsi_pk(habs) == "svc:kel#habs."


def test_nskey_backward_compatible_when_no_shared_args():
    """No shared args ⇒ every store uses the instance namespace (Phase C behavior)."""
    db = _dber(name="svc", namespace="svc:kel")
    kels = DynamoSubDb(name="kels.", table_name="core")
    assert db._pk(kels, b"AID") == f"svc:kel#kels.#{_hex(b'AID')}"


def test_meta_pk_of_shared_store_lands_in_shared_namespace():
    """A shared store's meta row PK uses the shared namespace; the version meta store stays private."""
    db = _dber(name="svc", namespace="svc:kel",
               shared_namespace="shared", shared_stores={"kels."})
    assert db._nskey("kels.") == "shared#kels."        # -> meta PK __meta__#shared#kels.
    assert db._nskey("__meta__") == "svc:kel#__meta__"  # version meta is per-service


def test_shared_namespace_rejects_hash():
    import pytest
    with pytest.raises(ValueError):
        _dber(name="svc", shared_namespace="bad#ns", shared_stores={"kels."})
```
Update the `_dber` helper at the top of the file to accept the new kwargs:
```python
def _dber(name="svc", namespace=None, shared_namespace=None, shared_stores=None):
    return DynamoDBer(name=name, stores={}, table_name="core",
                      client=None, table=None, namespace=namespace,
                      shared_namespace=shared_namespace, shared_stores=shared_stores)
```

- [ ] **Step 2: Run — expect FAIL** (`DynamoDBer.__init__() got an unexpected keyword argument 'shared_namespace'`)
Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing_namespace.py -q`

- [ ] **Step 3: Implement in `dynamodbing.py`.** In `__init__` (signature + body), add the params after `namespace`:
```python
    def __init__(self, *, name: str, stores: dict[str, DynamoSubDb],
                 table_name: str, client, table, namespace: str | None = None,
                 shared_namespace: str | None = None,
                 shared_stores=None):
        ...
        namespace = namespace if namespace else name
        if "#" in namespace:
            raise ValueError(f"namespace may not contain '#': {namespace!r}")
        self.namespace = namespace
        # Per-store routing: stores in `shared_stores` are keyed under
        # `shared_namespace` instead of `namespace`, so the public KEL/receipt/
        # key-state stores pool into one shared namespace (the key-state oracle)
        # while node-private stores stay per-service. Both off ⇒ unchanged.
        if shared_namespace and "#" in shared_namespace:
            raise ValueError(f"shared_namespace may not contain '#': {shared_namespace!r}")
        self._shared_namespace = shared_namespace
        self._shared_stores = frozenset(shared_stores or ())
        ...
```
Add the same two params (defaults `None`) to `open(...)`'s signature and pass them through to the `cls(...)` call:
```python
        namespace: str | None = None,
        shared_namespace: str | None = None,
        shared_stores=None,
    ) -> "DynamoDBer":
        ...
        dber = cls(name=name, stores=opened, table_name=table_name,
                   client=client, table=table, namespace=namespace,
                   shared_namespace=shared_namespace, shared_stores=shared_stores)
```
Replace `_nskey`:
```python
    def _nskey(self, name: str) -> str:
        """Prefix a store/meta name with its namespace. Stores listed in
        shared_stores route to shared_namespace (the pooled key-state oracle);
        all others use this instance's per-service namespace."""
        ns = (self._shared_namespace
              if self._shared_namespace and name in self._shared_stores
              else self.namespace)
        return f"{ns}#{name}"
```

- [ ] **Step 4: Run — expect PASS** (4 new tests). Then run the whole DB suite for no regression:
Run: `.venv/bin/python -m pytest tests/db -q`
Expected: PASS (all — backward compat holds).

- [ ] **Step 5: Commit**
```bash
git add src/keri/db/dynamodbing.py tests/db/test_dynamodbing_namespace.py
git commit -m "feat(dynamodbing): per-store namespace routing (shared_namespace/shared_stores)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `SHARED_KEL_STORES` constant + invariant test

**Files:**
- Modify: `src/keri/app/lambding.py` (after `BASER_STORES`, ~`:57`)
- Test: `tests/db/test_dynamodbing_namespace.py` (or `tests/app/test_lambding.py` if present — else co-locate)

- [ ] **Step 1: Write failing test** (append to `tests/db/test_dynamodbing_namespace.py`):
```python
def test_shared_kel_stores_is_public_subset_of_baser():
    from keri.app.lambding import BASER_STORES, REGER_STORES, SHARED_KEL_STORES
    baser = set(BASER_STORES)
    assert set(SHARED_KEL_STORES) <= baser, "shared set must be a subset of BASER_STORES"
    # must NOT share escrows, node registry, KRAM, OOBI, or any Reger store
    forbidden = {"habs.", "names.", "hbys.", "pses.", "pwes.", "ooes.", "udes.",
                 "ldes.", "ures.", "vres.", "exns.", "oobis."} | set(REGER_STORES)
    assert set(SHARED_KEL_STORES).isdisjoint(forbidden), "shared set leaks a private store"
    # the verifiable key-event/receipt/key-state core
    assert {"kels.", "evts.", "fels.", "sigs.", "wigs.", "rcts.", "stts.", "ksns."} \
        <= set(SHARED_KEL_STORES)
```

- [ ] **Step 2: Run — expect FAIL** (`ImportError: cannot import name 'SHARED_KEL_STORES'`)
Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing_namespace.py::test_shared_kel_stores_is_public_subset_of_baser -q`

- [ ] **Step 3: Implement** — add to `src/keri/app/lambding.py` immediately after the `BASER_STORES = [...]` block:
```python
# Public, AID-prefix-keyed key-event / receipt / key-state stores that are SAFE
# to pool into one shared namespace across services in a trust domain (the
# "key-state oracle"). A strict subset of BASER_STORES; excludes the node's hab
# registry, ALL escrows, KRAM/challenge, OOBI queues, and the entire Reger.
# See docs/superpowers/specs/2026-06-15-cdk-kel-oracle-design.md.
SHARED_KEL_STORES = frozenset({
    "evts.", "fels.", "kels.", "dtss.", "sigs.", "wigs.", "rcts.", "vrcs.",
    "aess.", "fons.", "wits.", "stts.", "ksns.", "knas.",
})
```

- [ ] **Step 4: Run — expect PASS.** Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing_namespace.py -q`

- [ ] **Step 5: Commit**
```bash
git add src/keri/app/lambding.py tests/db/test_dynamodbing_namespace.py
git commit -m "feat(lambding): SHARED_KEL_STORES — the public stores poolable into the oracle

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Witness handler shares its KEL stores

**Files:**
- Modify: `keri_cdk/handlers/witness/witness_handler.py` (the `DynamoDBer.open(...)` call, ~`:95-101`)
- Test: `tests/handlers/test_handler_namespace.py`

- [ ] **Step 1: Write failing test** (append):
```python
def test_witness_open_passes_shared_kel_stores(monkeypatch):
    """init() opens the Baser with shared_namespace='shared' + SHARED_KEL_STORES."""
    import keri_cdk.handlers.witness.witness_handler as wh
    from keri.app.lambding import SHARED_KEL_STORES
    captured = {}

    def fake_open(*a, **kw):
        captured.update(kw)
        raise SystemExit  # short-circuit init() right after the Baser open

    # Patch the SOURCE class method — works whether the handler imports DynamoDBer
    # at module top OR locally inside init() (both reference the same class object).
    monkeypatch.setattr("keri.db.dynamodbing.DynamoDBer.open", fake_open)
    monkeypatch.setenv("WITNESS_BASER_TABLE", "keri-core")
    monkeypatch.setenv("WITNESS_NAMESPACE", "KeriHostWitness:kel")
    wh._hby = None
    try:
        wh.init()
    except SystemExit:
        pass
    assert captured.get("shared_namespace") == "shared"
    assert captured.get("shared_stores") == SHARED_KEL_STORES
```

- [ ] **Step 2: Run — expect FAIL** (`shared_namespace` not in captured).
Run: `.venv/bin/python -m pytest tests/handlers/test_handler_namespace.py::test_witness_open_passes_shared_kel_stores -q`

- [ ] **Step 3: Implement** — in `witness_handler.py`, add the import near the other keri imports inside `init()` (where `BASER_STORES` is imported):
```python
    from keri.app.lambding import BASER_STORES, SHARED_KEL_STORES, setup_baser, setup_keeper
```
and pass the shared args to the open:
```python
    db = DynamoDBer.open(name=name, stores=BASER_STORES, table_name=baser_table,
                         namespace=_namespace(name),
                         shared_namespace="shared", shared_stores=SHARED_KEL_STORES,
                         **kwa)
```

- [ ] **Step 4: Run — expect PASS.** Then the handler suite: `.venv/bin/python -m pytest tests/handlers -q` (no regression).

- [ ] **Step 5: Commit**
```bash
git add keri_cdk/handlers/witness/witness_handler.py tests/handlers/test_handler_namespace.py
git commit -m "feat(cdk): witness shares its public KEL stores into the oracle namespace

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Mailbox handler shares its KEL stores (Mailboxer stays private)

**Files:**
- Modify: `keri_cdk/handlers/mailbox/mailbox_handler.py` (the `DynamoDBer.open(...)` call, ~`:186-193`)
- Test: `tests/handlers/test_handler_namespace.py`

- [ ] **Step 1: Write failing test** (append):
```python
def test_mailbox_open_passes_shared_kel_stores(monkeypatch):
    import keri_cdk.handlers.mailbox.mailbox_handler as mh
    from keri.app.lambding import SHARED_KEL_STORES
    captured = {}

    def fake_open(*a, **kw):
        captured.update(kw)
        raise SystemExit

    monkeypatch.setattr("keri.db.dynamodbing.DynamoDBer.open", fake_open)
    monkeypatch.setenv("MAILBOX_BASER_TABLE", "keri-core")
    monkeypatch.setenv("MAILBOX_NAMESPACE", "KeriHostMailbox:mbx")
    mh._hby = None
    try:
        mh.init()
    except SystemExit:
        pass
    assert captured.get("shared_namespace") == "shared"
    assert captured.get("shared_stores") == SHARED_KEL_STORES
    # Mailboxer stores are NOT in the shared set
    assert "tpcs." not in SHARED_KEL_STORES and "msgs." not in SHARED_KEL_STORES
```

- [ ] **Step 2: Run — expect FAIL.**
Run: `.venv/bin/python -m pytest tests/handlers/test_handler_namespace.py::test_mailbox_open_passes_shared_kel_stores -q`

- [ ] **Step 3: Implement** — in `mailbox_handler.py`, import `SHARED_KEL_STORES` (alongside the existing `BASER_STORES`/`MAILBOXER_STORES` import) and pass the shared args:
```python
    db = DynamoDBer.open(name=name, stores=baser_and_mbx_stores, table_name=baser_table,
                         namespace=_namespace(name),
                         shared_namespace="shared", shared_stores=SHARED_KEL_STORES,
                         **kwa)
```
(`baser_and_mbx_stores` includes the Mailboxer stores `tpcs.`/`msgs.`, which are NOT in `SHARED_KEL_STORES`, so they stay in the mailbox's private namespace.)

- [ ] **Step 4: Run — expect PASS.** Then `.venv/bin/python -m pytest tests/handlers -q`.

- [ ] **Step 5: Commit**
```bash
git add keri_cdk/handlers/mailbox/mailbox_handler.py tests/handlers/test_handler_namespace.py
git commit -m "feat(cdk): mailbox shares its public KEL stores (Mailboxer stays private)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Service-AID `db` shares KEL; `Reger` stays fully private

**Files:**
- Modify: `keri_cdk/handlers/serviceaid/runtime.py` (the `db` open, ~`:130-132`; leave the `reger` open ~`:134-136` unchanged)
- Test: `tests/serviceaid/` (add a focused test; mirror existing serviceaid test style)

- [ ] **Step 1: Write failing test** — create `tests/serviceaid/test_runtime_shared_kel.py`:
```python
"""The Service-AID db (Baser) shares its KEL stores; the reger (Reger) stays private."""
import keri_cdk.handlers.serviceaid.runtime as rt
from keri.app.lambding import SHARED_KEL_STORES


def test_serviceaid_db_open_shares_kel_reger_private(monkeypatch):
    calls = []

    def fake_open(*a, **kw):
        calls.append(kw)
        raise SystemExit

    monkeypatch.setattr("keri.db.dynamodbing.DynamoDBer.open", fake_open)
    # minimal cfg stub (init() reaches the `db` open after _dynamo_kwa(cfg),
    # which uses only region/endpoint_url); init signature is init(cfg=None).
    class Cfg:
        alias = "gated"; core_table = "keri-core"; kel_namespace = "gated:kel"
        tel_namespace = "gated:tel"; region = "us-east-1"; endpoint_url = None
    monkeypatch.setattr(rt, "_state", None, raising=False)
    try:
        rt.init(Cfg())
    except SystemExit:
        pass
    assert calls, "init() did not reach the db open"
    db_kw = calls[0]   # the FIRST open is the Baser `db` (shared); reger is second (private)
    assert db_kw.get("shared_namespace") == "shared"
    assert db_kw.get("shared_stores") == SHARED_KEL_STORES
```

- [ ] **Step 2: Run — expect FAIL.**
Run: `.venv/bin/python -m pytest tests/serviceaid/test_runtime_shared_kel.py -q`

- [ ] **Step 3: Implement** — in `runtime.py`, import `SHARED_KEL_STORES` and add the shared args to the **`db`** open only:
```python
    from keri.app.lambding import SHARED_KEL_STORES
    db = DynamoDBer.open(name=cfg.alias, stores=BASER_STORES + [PROC_STORE],
                         table_name=cfg.core_table, namespace=cfg.kel_namespace,
                         shared_namespace="shared", shared_stores=SHARED_KEL_STORES,
                         **kwa)
    setup_baser(db)
    reger = DynamoDBer.open(name=cfg.alias, stores=REGER_STORES,
                            table_name=cfg.core_table,
                            namespace=cfg.tel_namespace, **kwa)   # unchanged: fully private
```

- [ ] **Step 4: Run — expect PASS.** Then `.venv/bin/python -m pytest tests/serviceaid -q`.

- [ ] **Step 5: Commit**
```bash
git add keri_cdk/handlers/serviceaid/runtime.py tests/serviceaid/test_runtime_shared_kel.py
git commit -m "feat(cdk): Service-AID shares KEL into oracle; Reger (credential bodies) stays private

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: IAM — four-pattern LeadingKeys union on witness/mailbox/Service-AID

**Files:**
- Modify: `keri_cdk/witness_stack.py` (LeadingKeys list), `keri_cdk/mailbox_stack.py` (LeadingKeys list), `keri_cdk/service_aid.py` (`:218` LeadingKeys list)
- Test: `tests/cdk/test_witness_stack.py`, `tests/cdk/test_mailbox_stack.py`, `tests/cdk/test_service_aid.py`

- [ ] **Step 1: Write failing assertions** — add to `tests/cdk/test_witness_stack.py`:
```python
def test_witness_iam_grants_shared_and_private_leadingkeys():
    import json
    body = json.dumps(_synth().to_json())
    assert "shared#*" in body and "__meta__#shared#*" in body, \
        "witness must grant the shared-KEL oracle namespace"
```
Add the analogous `test_mailbox_iam_grants_shared_and_private_leadingkeys()` to `tests/cdk/test_mailbox_stack.py` (using its `_synth()`), and `test_service_aid_grants_shared_leadingkeys()` to `tests/cdk/test_service_aid.py`.

- [ ] **Step 2: Run — expect FAIL** (`shared#*` not present).
Run: `.venv/bin/python -m pytest tests/cdk/test_witness_stack.py::test_witness_iam_grants_shared_and_private_leadingkeys tests/cdk/test_mailbox_stack.py tests/cdk/test_service_aid.py -q`

- [ ] **Step 3: Implement** — in `witness_stack.py` and `mailbox_stack.py`, change the `dynamodb:LeadingKeys` list inside the table `PolicyStatement` to:
```python
                        "dynamodb:LeadingKeys": [
                            "shared#*",
                            "__meta__#shared#*",
                            f"{Aws.STACK_NAME}:*#*",
                            f"__meta__#{Aws.STACK_NAME}:*",
                        ]
```
In `service_aid.py` (`:218`), change its list to:
```python
                        "dynamodb:LeadingKeys": [
                            "shared#*",
                            "__meta__#shared#*",
                            f"{alias}:*#*",
                            f"__meta__#{alias}:*",
                        ]
```

- [ ] **Step 4: Run — expect PASS.** Then `.venv/bin/python -m pytest tests/cdk -q` (full CDK suite green).

- [ ] **Step 5: Commit**
```bash
git add keri_cdk/witness_stack.py keri_cdk/mailbox_stack.py keri_cdk/service_aid.py tests/cdk/
git commit -m "feat(cdk): grant shared-KEL + per-service LeadingKeys union for the oracle

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Oracle cross-service test (moto)

**Files:** Test: `tests/db/test_dynamodbing_namespace.py`

- [ ] **Step 1: Write the test** (append):
```python
@needs_moto
def test_shared_kel_oracle_cross_service_read_and_private_isolation():
    """Service A writes a counterparty KEL into the SHARED store; a separate
    service B reads it from `shared` (the oracle) — but B cannot see A's PRIVATE
    store rows."""
    from moto import mock_aws
    with mock_aws():
        a = DynamoDBer.open(name="A", stores=["kels.", "habs."], region="us-east-1",
                            table_name="keri-core", namespace="A:kel",
                            shared_namespace="shared", shared_stores={"kels."})
        b = DynamoDBer.open(name="B", stores=["kels.", "habs."], region="us-east-1",
                            table_name="keri-core", namespace="B:kel",
                            shared_namespace="shared", shared_stores={"kels."})
        a_kels, b_kels = a.env.open_db(b"kels."), b.env.open_db(b"kels.")
        a_habs, b_habs = a.env.open_db(b"habs."), b.env.open_db(b"habs.")
        a.setVal(a_kels, b"EXcounterparty", b"key-event")     # A writes shared KEL
        assert b.getVal(b_kels, b"EXcounterparty") == b"key-event"  # B reads via oracle
        a.setVal(a_habs, b"AownHab", b"secret")               # A writes PRIVATE store
        assert b.getVal(b_habs, b"AownHab") is None            # invisible to B
        a.close(); b.close()
```

- [ ] **Step 2: Run — expect PASS** (the routing from Task 1 makes this work).
Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing_namespace.py -q`

- [ ] **Step 3: Full local suite green**
Run: `.venv/bin/python -m pytest tests/cdk tests/handlers tests/db tests/serviceaid -q`

- [ ] **Step 4: Commit**
```bash
git add tests/db/test_dynamodbing_namespace.py
git commit -m "test(cdk): cross-service KEL oracle read + private-store isolation on one table

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Real-AWS validation + teardown (Docker + `AWS_PROFILE=personal`)

**Files:** uses `keri_cdk/layers/build_layer.sh`, `ecosystems/keri_host/`, `keri_cdk/probes/leadingkeys/`. No code changes.

- [ ] **Step 1: Build the real layer** — `bash keri_cdk/layers/build_layer.sh` (Docker; overwrites the placeholder).
- [ ] **Step 2: Deploy** `keri_host` (oracle now on) from the **repo root** via `--app` (the absolute-asset-path fix from Phase C means CWD no longer matters, but `--app` from root is the documented invocation):
```bash
AWS_PROFILE=personal npx --yes aws-cdk@latest deploy --all \
  --app ".venv/bin/python ecosystems/keri_host/app.py" --require-approval never \
  -c region=us-east-1 -c witness_domain=witc.keri.host -c mailbox_domain=mboxc.keri.host \
  -c hosted_zone_id=Z0070723WLKQKTOACN5H
```
(Use `npx aws-cdk@latest` if the global `cdk` CLI predates the lib's cloud-assembly schema.)
- [ ] **Step 3: Oracle read** — hit the witness (`GET /` → incept) so it writes its KEL into `shared#…`; then confirm a `shared#kels.#…` (and `shared#evts.#…`) row exists in `keri-core` (`aws dynamodb scan --table-name keri-core --projection-expression PK ... | grep '^shared#'`), proving the witness wrote to the shared namespace (not its private one).
- [ ] **Step 4: Concurrent-acceptance replay-clean check (the double-FEL benign assertion)** — drive two services accepting the same AID's events concurrently against the shared pool, then clone/replay that AID's KEL and assert it verifies clean end-to-end (a duplicate first-seen ordinal, if it occurs, must not break replay). Capture the result in the probe/run log.
- [ ] **Step 5: Two-grant LeadingKeys probe** — extend/run `keri_cdk/probes/leadingkeys/probe.py` so each tenant role carries the **four-pattern union**; assert: own-`shared` read/write ALLOW, own-private read/write ALLOW, **another tenant's private namespace DENY**, cross-tenant `shared` is shared-by-design (ALLOW). Record results in its README.
- [ ] **Step 6: Teardown** — `cdk destroy KeriHostWitness KeriHostMailbox` (retain+delete any stuck ACM cert as in Phase C), delete keeper secrets `keri/KeriHost*/keeper`, disable protections + delete the `keri-core` table, and remove leftover Route53 records. Confirm the live SAM federation (`serverless-witness`) is untouched.

---

## Completion

Full local suite green, then **superpowers:finishing-a-development-branch** to merge `feat/cdk-kel-oracle` → `development` (direct, no PR; matches Phase A/B/C). Update memory `project_kel_public_shared_oracle` to SHIPPED.
