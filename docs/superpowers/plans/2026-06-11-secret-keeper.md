# Secret-Backed Keeper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plaintext DynamoDB `-ks` keeper with a `SecretKeeper` — an in-memory keeper backend persisted as one KMS-encrypted secret per stack (`keri/<stack>/keeper` = `{salt, bran, zlib-compressed keeper blob}`), eliminating plaintext private keys at rest while leaving keripy's `Keeper`/`Manager`/aeid surface unchanged.

**Architecture:** A pure storage-backend substitution, exactly like the fork's `DynamoDBer`. `SecretKeeper` holds the keeper sub-databases in an in-memory dict (loaded from the secret at cold start, flushed back on the rare establishment write), implementing the small KV method surface the keeper's Subers call. A thin `SecretStore` wraps Secrets Manager (SSM-pluggable). The witness, mailbox, and Service AID runtimes swap their keeper to `SecretKeeper`; the `-ks` tables, the auto-minted bran secret, and the `WitnessSaltSecret`/`MailboxSaltSecret` CFN params are removed.

**Tech Stack:** Python 3.14, keripy (this fork), boto3 (Secrets Manager), zlib, moto (test), pytest. Spec: `docs/superpowers/specs/2026-06-11-secret-keeper-design.md`.

**Worktree / env:** Work in `~/code/keripy/.worktrees/secret-keeper` on `feat/secret-keeper`. Run tests with the worktree's own venv: `.venv/bin/python -m pytest`. The venv is NOT yet built — Task 1 Step 0 builds it. Default pytest import mode (no `tests/__init__.py` in `service-aid/tests`).

---

## Deviations from spec (flagged for the spec reviewer)

1. **Witness/mailbox get-or-create runs in the handler `init()`, not a new SAM Custom Resource** (the spec's open question #2 recommended a CR for a read-only request Lambda). Rationale: adding a SAM-backed Custom Resource per stack is heavy; the handlers already run `init()` lazily. Cost: the witness/mailbox request Lambda gets `secretsmanager:CreateSecret`+`PutSecretValue` IAM scoped to `keri/<stack>/*` (used only at first cold start / establishment). The Service AID keeps its **existing inception CR** for get-or-create (request Lambda stays read-only). The CR-hardening of witness/mailbox is a documented follow-up.
2. **Auto-flush on each keeper write** (rather than a batched `flush()`), because the `Manager` has no end-of-operation hook. Writes occur only at inception/rotation (establishment-only, verified), so this is a handful of `PutSecretValue` calls at deploy, never in the signing path.

---

## File Structure

**New (keripy core):**
- `src/keri/db/secretkeeper.py` — `SecretStore` (thin SM/SSM client), keeper (de)serialization helpers (`dumpKeeper`/`loadKeeper`), and `SecretKeeper` (in-memory keeper DBer + `SecretEnv`/`SecretSubDb`). One file, one responsibility: the secret-backed keeper.

**New test:**
- `tests/db/test_secretkeeper.py`

**Modified (Service AID framework):**
- `service-aid/serviceaid/runtime.py` — keeper opens `SecretKeeper` instead of the `-ks` `DynamoDBer`; read `{salt,bran}` from the keeper secret.
- `service-aid/serviceaid/cdk/inception.py` — get-or-create `keri/<alias>/keeper` before `runtime.init`.
- `service-aid/serviceaid/cdk/service_aid_construct.py` — drop the keeper `-ks` table + the auto-minted bran `sm.Secret`; grant the function read-only on the keeper secret, the CR create/put; drop `SERVICEAID_KEEPER_TABLE`.
- `service-aid/serviceaid/config.py` — replace `keeper_table` with `keeper_secret` (the `keri/<alias>/keeper` name).
- `service-aid/tests/{test_config,test_runtime,test_cdk_synth,test_handler_e2e,test_inception,test_integration_local}.py` — updated for the keeper-secret.

**Modified (witness / mailbox SAM):**
- `sam-witness/witness_handler.py`, `sam-witness/template.yaml`
- `sam-mailbox/mailbox_handler.py`, `sam-mailbox/template.yaml`, `sam-mailbox/tests/test_mailbox_handler.py`

---

## Phase 0 — keripy core: the SecretKeeper backend

### Task 1: `SecretStore` — thin secret-store client (get / put / get-or-create)

**Files:**
- Create: `src/keri/db/secretkeeper.py`
- Test: `tests/db/test_secretkeeper.py`

- [ ] **Step 0: Build the worktree venv** (one-time)

```bash
cd /Users/seriouscoderone/code/keripy/.worktrees/secret-keeper
python3 -m venv .venv
.venv/bin/pip install -e . --quiet
.venv/bin/pip install 'moto>=5.0' 'pytest>=9.0.2' pytest-shell --quiet
.venv/bin/python -c "import keri,os; print('keri ->', os.path.realpath(keri.__file__))"
```
Expected: prints the worktree's `src/keri/__init__.py` path.

- [ ] **Step 1: Write the failing test**

`tests/db/test_secretkeeper.py`:

```python
# -*- encoding: utf-8 -*-
"""Tests for the secret-backed keeper (SecretStore + SecretKeeper)."""
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")

from keri.db.secretkeeper import SecretStore


@needs_moto
def test_secretstore_get_absent_returns_none():
    with mock_aws():
        store = SecretStore(region="us-east-1")
        assert store.get("keri/svc/keeper") is None


@needs_moto
def test_secretstore_put_then_get_roundtrip():
    with mock_aws():
        store = SecretStore(region="us-east-1")
        store.put("keri/svc/keeper", '{"v":1}')
        assert store.get("keri/svc/keeper") == '{"v":1}'


@needs_moto
def test_secretstore_get_or_create_is_idempotent():
    with mock_aws():
        store = SecretStore(region="us-east-1")
        created1, val1 = store.get_or_create("keri/svc/keeper", lambda: '{"v":1,"n":1}')
        created2, val2 = store.get_or_create("keri/svc/keeper", lambda: '{"v":1,"n":2}')
        assert created1 is True and val1 == '{"v":1,"n":1}'
        assert created2 is False and val2 == '{"v":1,"n":1}'   # existing wins
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/db/test_secretkeeper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'keri.db.secretkeeper'`.

- [ ] **Step 3: Implement `SecretStore` in `src/keri/db/secretkeeper.py`**

```python
# -*- encoding: utf-8 -*-
"""
keri.db.secretkeeper module

A secret-backed, in-memory Keeper: the entire (small) keystore lives in one
KMS-encrypted secret per stack. Pure storage substitution for the LMDB/DynamoDB
keeper — keripy's Keeper/Manager/aeid surface is unchanged.
"""
from __future__ import annotations


class SecretStore:
    """Thin pluggable secret-store client. Secrets Manager by default; SSM
    Parameter Store SecureString selectable via kind='ssm' (interface-ready)."""

    def __init__(self, *, region: str = "us-east-1", endpoint_url: str | None = None,
                 kind: str = "secretsmanager", session=None):
        import boto3
        self.kind = kind
        kwa = {"region_name": region}
        if endpoint_url:
            kwa["endpoint_url"] = endpoint_url
        src = session if session is not None else boto3
        if kind == "secretsmanager":
            self._c = src.client("secretsmanager", **kwa)
        elif kind == "ssm":
            self._c = src.client("ssm", **kwa)
        else:
            raise ValueError(f"unknown secret store kind: {kind!r}")

    def get(self, name: str) -> str | None:
        """Return the secret string, or None if it does not exist."""
        if self.kind == "secretsmanager":
            try:
                return self._c.get_secret_value(SecretId=name)["SecretString"]
            except self._c.exceptions.ResourceNotFoundException:
                return None
        else:  # ssm
            try:
                return self._c.get_parameter(Name=name, WithDecryption=True
                                             )["Parameter"]["Value"]
            except self._c.exceptions.ParameterNotFound:
                return None

    def put(self, name: str, value: str) -> None:
        """Create-or-update the secret value."""
        if self.kind == "secretsmanager":
            try:
                self._c.put_secret_value(SecretId=name, SecretString=value)
            except self._c.exceptions.ResourceNotFoundException:
                self._c.create_secret(Name=name, SecretString=value)
        else:  # ssm
            self._c.put_parameter(Name=name, Value=value, Type="SecureString",
                                  Overwrite=True)

    def get_or_create(self, name: str, mint) -> tuple[bool, str]:
        """Return (created, value). If absent, store mint() and return it; else
        return the existing value (existing always wins — never overwrites)."""
        existing = self.get(name)
        if existing is not None:
            return False, existing
        value = mint()
        self.put(name, value)
        return True, value
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/db/test_secretkeeper.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/keri/db/secretkeeper.py tests/db/test_secretkeeper.py
git commit -m "feat(secretkeeper): SecretStore — thin SM/SSM get/put/get-or-create"
```

---

### Task 2: keeper blob (de)serialization — versioned, compressed

**Files:**
- Modify: `src/keri/db/secretkeeper.py`
- Test: `tests/db/test_secretkeeper.py`

The keeper is an in-memory dict `{subdb_name: {hex_key: value_bytes}}`. `dumpKeeper` → JSON → zlib → base64-ascii (for JSON transport inside the secret doc). `loadKeeper` reverses it. A leading `"v"` field versions the format.

- [ ] **Step 1: Write the failing test** (append to `tests/db/test_secretkeeper.py`)

```python
from keri.db.secretkeeper import dumpKeeper, loadKeeper


def test_keeper_blob_roundtrip_bytes_values():
    data = {"gbls.": {"6165696400": b"aeid-value"},   # hex key -> bytes val
            "pris.": {"deadbeef": b"\x00\x01\x02ciphertext"}}
    blob = dumpKeeper(data)
    assert isinstance(blob, str)                 # base64 ascii, JSON-safe
    assert loadKeeper(blob) == data              # exact round-trip incl bytes


def test_keeper_blob_empty():
    assert loadKeeper(dumpKeeper({})) == {}


def test_keeper_blob_none_loads_empty():
    assert loadKeeper(None) == {}
    assert loadKeeper("") == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/db/test_secretkeeper.py::test_keeper_blob_roundtrip_bytes_values -v`
Expected: FAIL — ImportError on `dumpKeeper`.

- [ ] **Step 3: Implement (add to `secretkeeper.py`)**

```python
import base64
import json
import zlib

_BLOB_VERSION = 1


def dumpKeeper(data: dict[str, dict[str, bytes]]) -> str:
    """Serialize the keeper dict to a compressed, base64-ascii blob.

    data: {subdb_name: {hex_key: value_bytes}}. Values are bytes (CESR);
    base64-encoded for JSON transport, then the whole doc is zlib-compressed.
    """
    payload = {"v": _BLOB_VERSION,
               "d": {sub: {k: base64.b64encode(v).decode("ascii")
                           for k, v in items.items()}
                     for sub, items in data.items()}}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(zlib.compress(raw)).decode("ascii")


def loadKeeper(blob: str | None) -> dict[str, dict[str, bytes]]:
    """Inverse of dumpKeeper. None/empty -> {} (fresh keeper)."""
    if not blob:
        return {}
    raw = zlib.decompress(base64.b64decode(blob))
    payload = json.loads(raw)
    if payload.get("v") != _BLOB_VERSION:
        raise ValueError(f"unsupported keeper blob version: {payload.get('v')}")
    return {sub: {k: base64.b64decode(v) for k, v in items.items()}
            for sub, items in payload["d"].items()}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/db/test_secretkeeper.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/keri/db/secretkeeper.py tests/db/test_secretkeeper.py
git commit -m "feat(secretkeeper): versioned compressed keeper blob (de)serialization"
```

---

### Task 3: `SecretKeeper` — in-memory keeper DBer

**Files:**
- Modify: `src/keri/db/secretkeeper.py`
- Test: `tests/db/test_secretkeeper.py`

`SecretKeeper` implements the **same KV method surface `DynamoDBer` exposes** (`getVal/setVal/putVal/remVal/getTopItemIter/cntAll/getValsIter/cntVals/close`) and an `env.open_db` returning a `SecretSubDb` handle — but over the in-memory dict, auto-flushing the whole secret doc (preserving `salt`/`bran`) on each mutation. IMPORTANT: before implementing, open `src/keri/db/dynamodbing.py` and confirm the exact signatures/return types of those methods (and which the keeper's Subers actually call — `Suber`, `CesrSuber`, `CryptSignerSuber`, `Komer`); mirror them precisely so the Subers work unchanged. Methods the keeper does not use (IoSet/IoDup/On*) raise `NotImplementedError`.

- [ ] **Step 1: Write the failing test** (append)

```python
from keri.db.secretkeeper import SecretKeeper, SecretStore


@needs_moto
def test_secretkeeper_kv_roundtrip_and_persist():
    from moto import mock_aws
    with mock_aws():
        store = SecretStore(region="us-east-1")
        ks = SecretKeeper(store=store, secret_name="keri/svc/keeper",
                          salt="0Asalt", bran="b" * 21)
        sub = ks.env.open_db(b"gbls.")
        assert ks.setVal(sub, b"aeid", b"Dpubkey") is True
        assert ks.getVal(sub, b"aeid") == b"Dpubkey"
        # putVal does not overwrite
        assert ks.putVal(sub, b"aeid", b"other") is False
        # mutation persisted to the secret, salt/bran preserved
        import json
        doc = json.loads(store.get("keri/svc/keeper"))
        assert doc["salt"] == "0Asalt" and doc["bran"] == "b" * 21

        # fresh keeper over the same secret reloads the value
        ks2 = SecretKeeper.open(store=store, secret_name="keri/svc/keeper")
        sub2 = ks2.env.open_db(b"gbls.")
        assert ks2.getVal(sub2, b"aeid") == b"Dpubkey"
        assert ks2.salt == "0Asalt" and ks2.bran == "b" * 21


@needs_moto
def test_secretkeeper_top_iter_and_rem():
    from moto import mock_aws
    with mock_aws():
        store = SecretStore(region="us-east-1")
        ks = SecretKeeper(store=store, secret_name="keri/svc/keeper",
                          salt="0Asalt", bran="b" * 21)
        sub = ks.env.open_db(b"pris.")
        ks.setVal(sub, b"k1", b"v1")
        ks.setVal(sub, b"k2", b"v2")
        items = dict(ks.getTopItemIter(sub))
        assert items == {b"k1": b"v1", b"k2": b"v2"}
        assert ks.remVal(sub, b"k1") is True
        assert ks.getVal(sub, b"k1") is None


def test_secretkeeper_unsupported_method_raises():
    # IoSet methods are not implemented (group-multisig keeper stores unused)
    ks = SecretKeeper(store=None, secret_name="x", salt=None, bran=None,
                      no_store=True)
    with pytest.raises(NotImplementedError):
        ks.getIoSetItemIter(None, b"k")
```

(Adapt `getTopItemIter`'s yield shape and `remVal`'s name to match DynamoDBer exactly if they differ — keep the assertions' meaning.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/db/test_secretkeeper.py -k secretkeeper -v`
Expected: FAIL — ImportError on `SecretKeeper`.

- [ ] **Step 3: Implement (add to `secretkeeper.py`)**

```python
class SecretSubDb:
    """A declared keeper sub-database handle (mirrors DynamoSubDb)."""
    def __init__(self, name: str):
        self.name = name
        self.dupsort = False
        self.opened = True

    def flags(self) -> dict:
        return {"dupsort": self.dupsort}


class SecretEnv:
    """Named sub-db opener used by subing/koming wrappers."""
    def __init__(self, owner: "SecretKeeper"):
        self.owner = owner

    def open_db(self, key, dupsort: bool = False) -> SecretSubDb:
        name = key.decode("utf-8") if isinstance(key, bytes) else key
        if name not in self.owner._subdbs:
            self.owner._subdbs[name] = SecretSubDb(name)
            self.owner._data.setdefault(name, {})
        return self.owner._subdbs[name]


def _hexk(key: bytes) -> str:
    return key.hex() if isinstance(key, (bytes, bytearray)) else bytes(key, "utf-8").hex()


class SecretKeeper:
    """In-memory keeper persisted as one KMS-encrypted secret. Implements the
    keeper-needed subset of the LMDBer/DynamoDBer interface; mutations
    auto-flush the whole secret doc (salt/bran preserved)."""

    def __init__(self, *, store, secret_name: str, salt, bran,
                 keeper_blob: str | None = None, no_store: bool = False):
        self.store = store
        self.secret_name = secret_name
        self.name = "keeper"
        self.salt = salt
        self.bran = bran
        self._no_store = no_store          # test escape hatch: skip flush
        self._data = loadKeeper(keeper_blob)   # {subdb: {hexkey: bytes}}
        self._subdbs: dict[str, SecretSubDb] = {n: SecretSubDb(n) for n in self._data}
        self.env = SecretEnv(self)
        self.opened = True
        self.temp = False
        self.readonly = False
        self.path = f"secret://{secret_name}"
        self._version = None

    @classmethod
    def open(cls, *, store, secret_name: str) -> "SecretKeeper":
        """Load an existing keeper secret (or empty) into a SecretKeeper."""
        import json
        raw = store.get(secret_name)
        doc = json.loads(raw) if raw else {"v": 1, "salt": None, "bran": None,
                                           "keeper": None}
        return cls(store=store, secret_name=secret_name, salt=doc.get("salt"),
                   bran=doc.get("bran"), keeper_blob=doc.get("keeper"))

    # ---- persistence ----
    def _flush(self):
        if self._no_store or self.store is None:
            return
        import json
        doc = {"v": 1, "salt": self.salt, "bran": self.bran,
               "keeper": dumpKeeper(self._data)}
        self.store.put(self.secret_name, json.dumps(doc, separators=(",", ":")))

    # ---- single-value CRUD (mirror DynamoDBer signatures) ----
    def putVal(self, db: SecretSubDb, key: bytes, val: bytes) -> bool:
        items = self._data.setdefault(db.name, {})
        hk = _hexk(key)
        if hk in items:
            return False
        items[hk] = bytes(val)
        self._flush()
        return True

    def setVal(self, db: SecretSubDb, key: bytes, val: bytes) -> bool:
        self._data.setdefault(db.name, {})[_hexk(key)] = bytes(val)
        self._flush()
        return True

    def getVal(self, db: SecretSubDb, key: bytes):
        return self._data.get(db.name, {}).get(_hexk(key))

    def remVal(self, db: SecretSubDb, key: bytes) -> bool:
        items = self._data.get(db.name, {})
        if _hexk(key) in items:
            del items[_hexk(key)]
            self._flush()
            return True
        return False

    def getTopItemIter(self, db: SecretSubDb, key: bytes = b""):
        """Yield (key_bytes, val_bytes) for all items in the subdb (optionally
        whose key startswith `key`). Keeper iteration is small + unordered."""
        prefix = _hexk(key) if key else ""
        for hk, v in list(self._data.get(db.name, {}).items()):
            if hk.startswith(prefix):
                yield (bytes.fromhex(hk), v)

    def cntAll(self, db: SecretSubDb) -> int:
        return len(self._data.get(db.name, {}))

    def close(self, clear: bool = False):
        if clear:
            self._data = {}
            self._flush()
        self.opened = False

    # ---- methods the keeper does not use (group-multisig / ordinal) ----
    def _unsupported(self, *a, **k):
        raise NotImplementedError(
            "SecretKeeper implements only the single-value keeper surface; "
            "IoSet/IoDup/ordinal methods are unused by the keeper")
    getIoSetItemIter = getIoSetVals = addIoSetVal = putIoSetVals = _unsupported
    getOnItem = getOnVal = putOnVal = appendOnVal = _unsupported
```

NOTE: confirm against `dynamodbing.py` whether the keeper Subers also call `getValsIter`/`cntVals`/`getValLast`; if so, add minimal dict-backed versions (a single-value keeper has no dups, so `getValsIter` yields the one value, `cntVals` returns 0/1). Add them only if a Suber actually calls them (the failing test will tell you).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/db/test_secretkeeper.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/keri/db/secretkeeper.py tests/db/test_secretkeeper.py
git commit -m "feat(secretkeeper): in-memory keeper DBer over a single KMS-encrypted secret"
```

---

### Task 4: keystone integration — Habery incept → reload → sign → verify

**Files:**
- Test: `tests/db/test_secretkeeper.py`

This is the proof the storage swap works: a real `Habery` with `ks=SecretKeeper` incepts an AID, the keeper persists to the (moto) secret, a fresh cold start reloads it, and signing produces a verifiable signature. Also pins that signing performs **no** secret write.

- [ ] **Step 1: Write the failing/keystone test** (append)

```python
@needs_moto
def test_habery_incepts_and_signs_over_secretkeeper():
    from moto import mock_aws
    from keri.app.habbing import Habery
    from keri.app.lambding import setup_keeper
    from keri.db.basing import Baser  # temp LMDB Baser for the public side
    with mock_aws():
        store = SecretStore(region="us-east-1")
        # get-or-create the keeper secret with a fresh salt+bran
        from keri.core.signing import Salter
        salt = Salter(raw=b'0123456789abcdef').qb64
        bran = "b" * 21
        import json
        store.put("keri/svc/keeper", json.dumps(
            {"v": 1, "salt": salt, "bran": bran, "keeper": None}))

        ks = SecretKeeper.open(store=store, secret_name="keri/svc/keeper")
        setup_keeper(ks)
        hby = Habery(name="svc", temp=True, ks=ks, salt=salt, bran=bran)
        hab = hby.makeHab(name="svc", transferable=True)
        pre = hab.pre
        assert ks.bran == bran
        # keeper persisted: a 2nd SecretKeeper over the same secret has the keys
        ks2 = SecretKeeper.open(store=store, secret_name="keri/svc/keeper")
        assert ks2.cntAll(ks2.env.open_db(b"pris.")) >= 1   # private seeds present
        hby.close()

        # cold start over the persisted keeper: load Hab, sign, verify
        ks3 = SecretKeeper.open(store=store, secret_name="keri/svc/keeper")
        setup_keeper(ks3)
        hby3 = Habery(name="svc", temp=True, ks=ks3, salt=salt, bran=bran)
        # the Hab must be recoverable and able to sign
        hab3 = hby3.habByName("svc") or hby3.makeHab(name="svc", transferable=True)
        sigs = hab3.sign(b"hello world")
        assert sigs and hab3.kever.verfers[0].verify(sigs[0].raw, b"hello world")
        hby3.close()
```

NOTE (high-risk, expect iteration): the Hab-reload-across-cold-start semantics depend on how keripy's `Habery.setup`/`loadHabs` reconcile a temp Baser with a persisted keeper. If `habByName` returns None on the fresh Habery (because the temp Baser is empty), the test's `or makeHab` re-incepts deterministically from the same salt → same `pre` → still valid for proving the keeper round-trips and signs. The load-bearing assertions are: (a) the keeper persisted real key material to the secret, and (b) a SecretKeeper-backed Habery can sign verifiably. If deeper Hab/Baser reload wiring is needed, mirror `sam-witness/witness_handler.py`'s init (which reloads a Hab from a persisted Baser) — but keep the public Baser out of scope here (this task proves the KEEPER backend; the deploy tasks wire the real Baser).

- [ ] **Step 2–4: iterate to green**

Run: `.venv/bin/python -m pytest tests/db/test_secretkeeper.py::test_habery_incepts_and_signs_over_secretkeeper -v`
Use the systematic-debugging skill if the keeper surface is incomplete (a missing method surfaces as an AttributeError from a Suber — add the minimal dict-backed version per Task 3's NOTE). Do NOT weaken the sign+verify assertion.

- [ ] **Step 5: Run the full secretkeeper suite + a keripy keeper regression**

```bash
.venv/bin/python -m pytest tests/db/test_secretkeeper.py -q
.venv/bin/python -m pytest tests/app/test_keeping.py -q   # ensure core keeper tests unaffected
```
Expected: secretkeeper green; `test_keeping.py` unchanged (we added a backend, touched no core).

- [ ] **Step 6: Commit**

```bash
git add tests/db/test_secretkeeper.py
git commit -m "test(secretkeeper): Habery incept->reload->sign over SecretKeeper (moto)"
```

---

## Phase 1 — Service AID swap

### Task 5: `runtime.py` — keeper on SecretKeeper; config keeper_secret

**Files:**
- Modify: `service-aid/serviceaid/config.py`, `service-aid/serviceaid/runtime.py`
- Test: `service-aid/tests/test_config.py`, `service-aid/tests/test_runtime.py`

- [ ] **Step 1: Update config test** — replace `keeper_table` with `keeper_secret` in `service-aid/tests/test_config.py` (the `test_config_from_env` assertions + env): set `SERVICEAID_KEEPER_SECRET` and assert `cfg.keeper_secret == "keri/rating/keeper"`; drop `SERVICEAID_KEEPER_TABLE`/`cfg.keeper_table`.

- [ ] **Step 2: Run → fail.** `.venv/bin/python -m pytest service-aid/tests/test_config.py -v` → FAIL (attribute/env mismatch).

- [ ] **Step 3: Edit `config.py`** — in `Config`: remove `keeper_table`, add `keeper_secret: str = ""`. In `from_env`: read `SERVICEAID_KEEPER_SECRET` (default `f"keri/{alias}/keeper"` when unset, since it's convention-derived from the alias/stack). Keep `kel_namespace`/`tel_namespace` as-is.

```python
        alias = os.environ["SERVICEAID_ALIAS"]
        ...
        keeper_secret=os.environ.get("SERVICEAID_KEEPER_SECRET") or f"keri/{alias}/keeper",
```

- [ ] **Step 4: Edit `runtime.py`** — replace the keeper `DynamoDBer` with `SecretKeeper`:

```python
from keri.db.secretkeeper import SecretStore, SecretKeeper
...
    # Keeper: one KMS-encrypted secret per stack (NOT the pooled DynamoDB table).
    store = SecretStore(region=cfg.region, endpoint_url=cfg.endpoint_url)
    ks = SecretKeeper.open(store=store, secret_name=cfg.keeper_secret)
    setup_keeper(ks)
    bran = ks.bran   # the bran lives in the keeper secret (loaded above)
    ...
    hby = Habery(name=cfg.alias, temp=False, free=True, db=db, ks=ks, cf=cf,
                 salt=ks.salt, bran=ks.bran)
```
Remove the old keeper-DynamoDBer open + the `load_bran` call + the `KEEPER_STORES` keeper table. (The salt+bran now come from the keeper secret, provisioned by the inception CR in Task 6. If `ks.bran` is None — secret not yet provisioned — log the branless warning as before.)

- [ ] **Step 5: Update `test_runtime.py`** — the moto setup creates the keeper secret instead of a Secrets-Manager bran:

```python
        store_doc = {"v":1, "salt": Salter(raw=b'0123456789abcdef').qb64,
                     "bran": "x"*21, "keeper": None}
        import boto3, json
        boto3.client("secretsmanager", region_name="us-east-1").create_secret(
            Name="keri/rating/keeper", SecretString=json.dumps(store_doc))
```
and `_cfg(keeper_secret="keri/rating/keeper")` (drop `keeper_table`). Keep the assertions: transferable AID incepts, `state.hby.ks.bran is not None` (encryption engaged), registry exists, warm-idempotent.

- [ ] **Step 6: Run** `.venv/bin/python -m pytest service-aid/tests/test_config.py service-aid/tests/test_runtime.py -v` → PASS. Then full suite `.venv/bin/python -m pytest service-aid/tests/ -q` (fix any test still referencing `keeper_table`; expect green).

- [ ] **Step 7: Commit**

```bash
git add service-aid/serviceaid/config.py service-aid/serviceaid/runtime.py service-aid/tests/
git commit -m "feat(serviceaid): runtime keeper on SecretKeeper (one secret per stack)"
```

---

### Task 6: inception CR get-or-create + construct drops -ks table & bran secret

**Files:**
- Modify: `service-aid/serviceaid/cdk/inception.py`, `service-aid/serviceaid/cdk/service_aid_construct.py`
- Test: `service-aid/tests/test_inception.py`, `service-aid/tests/test_cdk_synth.py`

- [ ] **Step 1: Update inception test** — the CR get-or-creates `keri/<alias>/keeper` (generating salt+bran) before `runtime.init`. In `test_inception.py`, instead of pre-creating a bran secret, assert the CR creates the keeper secret and returns the pre. Setup: moto; env `SERVICEAID_KEEPER_SECRET=keri/rating/keeper` (or default). After `on_event({"RequestType":"Create"})`, assert `boto3 secretsmanager get_secret_value(SecretId="keri/rating/keeper")` exists and its JSON has non-empty `salt`+`bran`+`keeper`.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Edit `inception.py`** — before `runtime.init()`, get-or-create the keeper secret:

```python
def on_event(event, context):
    request_type = event["RequestType"]
    if request_type in ("Create", "Update"):
        from serviceaid.config import Config
        from keri.db.secretkeeper import SecretStore
        from keri.core.signing import Salter
        import json
        cfg = Config.from_env()
        store = SecretStore(region=cfg.region, endpoint_url=cfg.endpoint_url)
        store.get_or_create(cfg.keeper_secret, lambda: json.dumps(
            {"v": 1, "salt": Salter().qb64,
             "bran": Salter().qb64[2:].replace("/", "_")[:21] or ("a"*21),
             "keeper": None}))
        runtime.reset()
        state = runtime.init()
        return {"PhysicalResourceId": state.hab.pre,
                "Data": {"ServiceAidPre": state.hab.pre}}
    return {"PhysicalResourceId": event.get("PhysicalResourceId", "noop")}
```
(Use a ≥21-char random bran; a `Salter().qb64` is 24 chars of base64 — slice to a clean ≥21-char passcode. Keep it simple and ≥21.)

- [ ] **Step 4: Edit `service_aid_construct.py`** — remove the keeper `-ks` `ddb.Table` and the `sm.Secret` bran; add a keeper-secret name + scoped IAM:
  - Drop `keeper_table` creation, `keeper_table.grant_read_write_data(fn)`, the `KeeperBran` `sm.Secret`, `bran.grant_read(fn)`, and `SERVICEAID_KEEPER_TABLE`/`SERVICEAID_BRAN_SECRET` env.
  - Add env `SERVICEAID_KEEPER_SECRET: f"keri/{alias}/keeper"`.
  - Request function IAM: **read-only** on the keeper secret: `secretsmanager:GetSecretValue` on `arn:…:secret:keri/{alias}/*`.
  - The inception CR (uses the same fn as `on_event_handler`) needs create/put: add `secretsmanager:CreateSecret`,`PutSecretValue`,`DescribeSecret` on `keri/{alias}/*` to the function role (the fn doubles as the CR handler, so it carries both — acceptable; note in a comment that a dedicated CR role would tighten this).

```python
        env = {
            "SERVICEAID_ALIAS": alias,
            "SERVICEAID_CORE_TABLE": core_table_name,
            "SERVICEAID_KEEPER_SECRET": f"keri/{alias}/keeper",
            "SERVICEAID_WITNESSES": ",".join(witnesses),
            "SERVICEAID_TOAD": str(toad),
            "SERVICEAID_HANDLER": handler_module,
            "SERVICEAID_ALLOWLIST": ",".join(allowlist or []),
            "SERVICEAID_REQUIRED_SCHEMA": required_schema,
            "SERVICEAID_REGION": self.node.try_get_context("region") or "us-east-1",
            "LD_LIBRARY_PATH": "/var/task/lib",
        }
        ...
        keeper_secret_arn = (f"arn:aws:secretsmanager:*:*:secret:keri/{alias}/*")
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret",
                     "secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"],
            resources=[keeper_secret_arn]))
```

- [ ] **Step 5: Update `test_cdk_synth.py`** — drop the keeper-table count expectation (no more `-ks` DDB table) and the `KeeperBran` secret; assert the function env has `SERVICEAID_KEEPER_SECRET` and an IAM statement granting `secretsmanager:GetSecretValue` on `keri/rating/*`. Adjust `t.resource_count_is("AWS::DynamoDB::Table", ...)` (the construct now creates 0 tables — it references the core table and no longer makes a keeper table) and `AWS::SecretsManager::Secret` (0 — the secret is created at runtime by the CR, not in CFN).

- [ ] **Step 6: Run** `.venv/bin/python -m pytest service-aid/tests/ -q` → all green (fix any remaining `keeper_table`/`bran` references across the e2e/integration tests). Expected: the suite passes with the keeper-secret model.

- [ ] **Step 7: Commit**

```bash
git add service-aid/serviceaid/cdk/ service-aid/tests/
git commit -m "feat(serviceaid): inception CR get-or-creates keeper secret; drop -ks table + bran secret"
```

---

## Phase 2 — Witness swap

### Task 7: witness handler + template to SecretKeeper

**Files:**
- Modify: `sam-witness/witness_handler.py`, `sam-witness/template.yaml`

- [ ] **Step 1: Edit `witness_handler.py` `init()`** — replace the keeper `DynamoDBer` (`-ks`) + `_load_salt` with `SecretKeeper` get-or-create on `keri/<stack>/keeper`:

```python
    from keri.db.secretkeeper import SecretStore, SecretKeeper
    from keri.core.signing import Salter
    import json
    keeper_secret = os.environ.get("WITNESS_KEEPER_SECRET") or f"keri/{name}/keeper"
    store = SecretStore(region=region, endpoint_url=endpoint_url or None)
    store.get_or_create(keeper_secret, lambda: json.dumps(
        {"v": 1, "salt": Salter().qb64, "bran": Salter().qb64[2:23], "keeper": None}))
    ks = SecretKeeper.open(store=store, secret_name=keeper_secret)
    setup_keeper(ks)
    ...
    _hby = Habery(name=name, temp=False, free=True, db=db, ks=ks, cf=cf,
                  salt=ks.salt, bran=ks.bran)
```
Remove the `WITNESS_KEEPER_TABLE` DynamoDBer keeper open, `_load_salt`, and the `WITNESS_SALT`/`WITNESS_SALT_SECRET` reads. Keep the partial-init `_clear_keeper` recovery (now operating on the SecretKeeper — `ks._data` clear; verify it still applies or drop if the in-memory keeper makes it moot — note which).

- [ ] **Step 2: Edit `sam-witness/template.yaml`** — remove the `WitnessKeeperTable` DynamoDB table, the `WitnessSaltSecret` param + its `WITNESS_SALT_SECRET` env + the conditional secrets policy + `HasSaltSecret` condition; remove the keeper `DynamoDBCrudPolicy`. Add: env `WITNESS_KEEPER_SECRET: !Sub "keri/${AWS::StackName}/keeper"` and an IAM policy granting `secretsmanager:GetSecretValue`/`DescribeSecret`/`CreateSecret`/`PutSecretValue` on `arn:${AWS::Partition}:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:keri/${AWS::StackName}/*`. (Per the Deviations note, the handler does get-or-create, so the function needs create/put — scoped to its own `keri/<stack>/*`.) Keep the Baser table.

- [ ] **Step 3: Verify** — no unit-test harness exists for the witness; validate: `.venv/bin/python -c "import ast; ast.parse(open('sam-witness/witness_handler.py').read()); print('ok')"`, and confirm no stale `WITNESS_SALT`/`WITNESS_KEEPER_TABLE` references: `grep -rn "WITNESS_SALT\|WITNESS_KEEPER_TABLE\|WitnessSalt\|WitnessKeeperTable" sam-witness/`. Update `sam-witness/env.json`/`samconfig.toml` references accordingly (env.json: drop WITNESS_SALT, set WITNESS_KEEPER_SECRET for local). YAML: `.venv/bin/python -c "import yaml" ` is not enough for CFN tags — note that `sam validate`/`cfn-lint` should run at deploy.

- [ ] **Step 4: Commit**

```bash
git add sam-witness/
git commit -m "feat(sam-witness): keeper on SecretKeeper secret; drop -ks table + salt param"
```

---

## Phase 3 — Mailbox swap

### Task 8: mailbox handler + template to SecretKeeper

**Files:**
- Modify: `sam-mailbox/mailbox_handler.py`, `sam-mailbox/template.yaml`, `sam-mailbox/tests/test_mailbox_handler.py`

- [ ] **Step 1: Edit `mailbox_handler.py` `init()`** — same swap as the witness (Task 7 Step 1) with `MAILBOX_*` names and `keri/<stack>/keeper`. The mailbox previously *required* a salt; now it get-or-creates the keeper secret (so it always has one) — keep a guard that raises if `SecretStore`/secret access fails, but the get-or-create removes the "missing salt" path. Replace `_load_salt`.

- [ ] **Step 2: Edit `sam-mailbox/template.yaml`** — same as witness Task 7 Step 2 with `Mailbox*`/`MAILBOX_*`: drop `MailboxKeeperTable`, `MailboxSaltSecret` param + env + policy + `HasSaltSecret`; add `MAILBOX_KEEPER_SECRET: !Sub "keri/${AWS::StackName}/keeper"` + the scoped secrets IAM. Keep the Baser table.

- [ ] **Step 3: Update `sam-mailbox/tests/test_mailbox_handler.py`** — the `_load_salt` tests and `test_init_requires_mailbox_salt` are replaced: add `_load_salt` removal; add a test that `init()` get-or-creates the keeper secret (moto) and that the keeper is SecretKeeper-backed. Keep the streaming/handler tests unchanged.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest sam-mailbox/tests/test_mailbox_handler.py -q` (install `pytest-asyncio` in the venv first: `.venv/bin/pip install pytest-asyncio`). Expected: green. Confirm no stale `MAILBOX_SALT`/`MailboxKeeperTable` refs.

- [ ] **Step 5: Commit**

```bash
git add sam-mailbox/
git commit -m "feat(sam-mailbox): keeper on SecretKeeper secret; drop -ks table + salt param"
```

---

## Final: whole-branch review

- [ ] Dispatch a final code reviewer over the whole `feat/secret-keeper` diff (base = `development` tip): focus on (a) the `SecretKeeper` KV surface completeness vs the keeper Subers, (b) auto-flush correctness + salt/bran preservation, (c) read-only-vs-create/put IAM scoping, (d) no plaintext keys anywhere, (e) the three deploy swaps are consistent. Then `superpowers:finishing-a-development-branch`.

---

## Self-review notes (author)

- **Spec coverage:** §4 SecretKeeper → Tasks 1–3; §5 layout/serialization → Task 2; §6 cold-start/write flow → Tasks 3–4 (+ deploy Tasks 5–8); §3.6 get-or-create-at-deploy → Task 6 (Service AID CR) + Tasks 7/8 (handler get-or-create, per the flagged deviation); §9 supersession (drop -ks/bran/salt-param) → Tasks 5–8; §10 testing → Tasks 1–4 + per-swap; §7 method surface → Task 3. The SSM path is interface-only (Task 1), per §11 OUT.
- **Highest risk:** Task 4 (Habery-over-SecretKeeper reload/sign) — the keeper KV surface may be missing a method a Suber calls; the failing test surfaces it as an AttributeError → add the minimal dict-backed version. Do not weaken sign+verify.
- **Type consistency:** `SecretStore.get/put/get_or_create`, `SecretKeeper.open/getVal/setVal/putVal/remVal/getTopItemIter/cntAll/close/.salt/.bran`, `dumpKeeper/loadKeeper`, and `cfg.keeper_secret` are used identically across tasks.
