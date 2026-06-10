# KERI Service AID Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A templated, AWS-CDK-packaged, serverless framework that wraps any in-process Python function as an autonomous KERI **Service AID** — verifying a signed `exn` caller via self-contained CESR, authorizing, dispatching to the developer's function, and replying with a signed **ACDC** delivered as an IPEX grant.

**Architecture:** Generalize the deployed `sam-witness` Lambda. Keep its serverless KERI core (Habery on `DynamoDBer` via `keri/app/lambding.py`, warm module singleton, API Gateway → Lambda → DynamoDB) and swap the witness's fixed receipt-compute for a developer-supplied function, adding a keeper-encrypted transferable+witnessed AID, an authorize gate, and synchronous ACDC issuance. Two-layer CDK topology: a shared `KeriCoreStack` (one pooled DynamoDB table holding Tier-1 public KERI state for all services, namespaced per service) plus thin per-service stacks (Lambda + API Gateway + scoped IAM + isolated encrypted keeper).

**Tech Stack:** Python 3.14, keripy (this fork), `DynamoDBer`, boto3, AWS Secrets Manager, `aws-cdk-lib` (Python CDK), Docker container Lambda, `moto` (test mock), pytest.

**Spec:** `docs/superpowers/specs/2026-06-10-keri-service-aid-framework-design.md`

**Worktree / env:** Work in `~/code/keripy/.worktrees/service-aid-framework` on branch `feat/service-aid-framework`. Run all tests with the worktree's isolated venv: `.venv/bin/python -m pytest`. **Never** run `pip install -e .` against the main checkout's global Python — it re-points the shared editable install.

---

## Deviations from the spec (deliberate, flagged for the spec reviewer)

These refine the spec's §4 layout based on code investigation done while planning. They do not change scope or behavior.

1. **One importable package.** The spec shows sibling `serviceaid/` and `cdk/` dirs. We consolidate CDK under `serviceaid.cdk` so the example app can `from serviceaid.cdk import ServiceAid, KeriCoreStack` after a single `pip install -e service-aid`.
2. **`handler.py` split into focused files.** The spec's single `handler.py` (init + handler) becomes `runtime.py` (cold-start `init()`, warm singletons, CESR→Request verification) + `handler.py` (Lambda entry, routing, reply framing) + `idempotency.py`. Focused files are easier to test and reason about.
3. **Reger is pooled into the shared core table** (spec §14 open question) — TEL/credential state is public Tier-1 KERI state, so it belongs in the shared table.
4. **Within one service, the Baser and Reger DynamoDBers use distinct namespaces** (`{alias}:kel` and `{alias}:tel`). This is **required for correctness**, not just multi-tenancy: `setup_baser` and `setup_reger` both define a `states`/`stts.` store (plus `ssgs.`/`scgs.`), so without separate namespaces their keys collide in one table. The namespacing change (Task 1) is what makes pooling both safe.
5. **v1 issuance uses a no-backer (no-TEL-witness) registry.** TEL events need no receipts, so issuance completes synchronously in the Lambda. The AID's *KEL* anchor is still witnessed by the federation; v1 collects those receipts best-effort with a bounded wait (spec §14 high-rate serialization is v2).

---

## File Structure

**Modified (keripy core):**
- `src/keri/db/dynamodbing.py` — add a `namespace` constructor/`open()` param and prefix the key formatters. Backward compatible (empty namespace ⇒ today's exact behavior, so the deployed witness is untouched).

**New framework package** `service-aid/` (beside `sam-witness/`):
- `service-aid/pyproject.toml` — declares the `serviceaid` package + dev deps (`moto`, `aws-cdk-lib`, `constructs`, `pytest`).
- `service-aid/serviceaid/__init__.py` — re-exports `service`, `Request`, `Reply`.
- `service-aid/serviceaid/contract.py` — developer API: `Service` registry + `@service.command`, `Request`, `Reply`, plus an in-memory `TestRuntime` for unit tests.
- `service-aid/serviceaid/config.py` — `Config.from_env()` and `load_bran()` (Secrets Manager).
- `service-aid/serviceaid/issuing.py` — synchronous ACDC issuance + IPEX-grant framing (adapted from the proven Locksmith path).
- `service-aid/serviceaid/authorize.py` — allowlist + required-credential policy.
- `service-aid/serviceaid/idempotency.py` — `seen()` / `record()` on a `proc.` store.
- `service-aid/serviceaid/runtime.py` — cold-start `init()`, warm singletons, `verify(event) -> Request`.
- `service-aid/serviceaid/handler.py` — generic Lambda entry: route → authorize → dispatch → reply, HTTP framing.
- `service-aid/serviceaid/cdk/__init__.py`
- `service-aid/serviceaid/cdk/keri_core_stack.py` — shared `KeriCoreStack` (pooled table + SSM export).
- `service-aid/serviceaid/cdk/service_aid_construct.py` — `ServiceAid` construct.
- `service-aid/serviceaid/cdk/inception.py` — Custom Resource handler: incept AID + registry on stack create.
- `service-aid/bootstrap.py` — libsodium shim + import handler (mirrors `sam-witness/bootstrap.py`).
- `service-aid/Dockerfile` — container image (mirrors `sam-witness/Dockerfile`).
- `service-aid/requirements.txt` — runtime deps for the image.
- `service-aid/examples/rating_engine/handler.py` — reference `@service.command`.
- `service-aid/examples/rating_engine/schema/rating_result.json` — ACDC schema SAD.
- `service-aid/examples/rating_engine/app.py` — CDK app (KeriCoreStack + ServiceAid).
- `service-aid/tests/conftest.py` — shared fixtures (temp Habery, saidified schema, moto core table).
- `service-aid/tests/test_*.py` — one per module.

**New keripy test:**
- `tests/db/test_dynamodbing_namespace.py` — namespacing unit + moto collision tests.

---

## Phase 0 — Foundations (prerequisites)

### Task 1: Per-tenant key namespacing in `DynamoDBer`

**Why:** Pooling many services (and the Baser + Reger of one service) into one table requires a tenant prefix on the partition key. Today the key is `{subdb}#{hex(key)}` with the service name only in the *table* name (`dynamodbing.py:329-339`), so two namespaces in one table collide. The change is backward compatible: empty namespace reproduces today's keys exactly, so the deployed witness keeps working.

**Files:**
- Modify: `src/keri/db/dynamodbing.py` (`__init__` ~line 193, `open` ~line 208, `_pk`/`_gsi_pk` lines 329-339, `_put_meta`/`_get_meta` lines 457-483, `_clear_store` lines 485-507)
- Create: `tests/db/test_dynamodbing_namespace.py`

- [ ] **Step 1: Write the failing formatter unit tests** (no AWS needed — pure string formatting)

Create `tests/db/test_dynamodbing_namespace.py`:

```python
# -*- encoding: utf-8 -*-
"""Tenant-namespacing tests for DynamoDBer key formatters."""
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri.db.dynamodbing import DynamoDBer, DynamoSubDb, _hex

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def _bare_dber(namespace=""):
    """A DynamoDBer with no live AWS resources — only the pure formatters
    are exercised, which never touch the client/table."""
    return DynamoDBer(name="svc", stores={}, table_name="core",
                      client=None, table=None, namespace=namespace)


def test_pk_legacy_no_namespace_unchanged():
    db = _bare_dber(namespace="")
    sub = DynamoSubDb(name="kels.", table_name="core")
    assert db._pk(sub, b"AID") == f"kels.#{_hex(b'AID')}"
    assert db._gsi_pk(sub) == "kels."


def test_pk_namespaced():
    db = _bare_dber(namespace="rating:kel")
    sub = DynamoSubDb(name="kels.", table_name="core")
    assert db._pk(sub, b"AID") == f"rating:kel#kels.#{_hex(b'AID')}"
    assert db._gsi_pk(sub) == "rating:kel#kels."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing_namespace.py -v`
Expected: FAIL — `DynamoDBer.__init__() got an unexpected keyword argument 'namespace'`.

- [ ] **Step 3: Add the `namespace` parameter and namespacing helper**

In `src/keri/db/dynamodbing.py`, change `__init__` (line 193) to accept and store `namespace`:

```python
    def __init__(self, *, name: str, stores: dict[str, DynamoSubDb],
                 table_name: str, client, table, namespace: str = ""):
        self.name = name
        self.namespace = namespace
        self.env = DynamoEnv(self)
```
(leave the rest of `__init__` unchanged)

Add `namespace` to `open()` (line 208) and forward it to the constructor (line 264):

```python
    @classmethod
    def open(
        cls,
        name: str,
        stores: list[str],
        *,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        table_name: str | None = None,
        clear: bool = False,
        session: "boto3.Session | None" = None,
        namespace: str = "",
    ) -> "DynamoDBer":
```
```python
        dber = cls(name=name, stores=opened, table_name=table_name,
                   client=client, table=table, namespace=namespace)
```

Add a helper just above `_pk` (line 329) and rewrite `_pk`/`_gsi_pk`:

```python
    def _nskey(self, name: str) -> str:
        """Prefix a store/meta name with the tenant namespace when set.

        Empty namespace reproduces the legacy single-tenant key format, so
        existing tables (e.g. the deployed witness) are unaffected.
        """
        return f"{self.namespace}#{name}" if self.namespace else name

    def _pk(self, db: DynamoSubDb, key: bytes) -> str:
        """Form the partition key: [namespace#]subdb_name#hex(key)."""
        return f"{self._nskey(db.name)}#{_hex(key)}"

    def _gsi_pk(self, db: DynamoSubDb) -> str:
        """GSI partition key is [namespace#]subdb_name."""
        return self._nskey(db.name)
```
(`_gsi_sk` is unchanged: the GSI partition key now carries the namespace, so the sort key stays the plain hex.)

- [ ] **Step 4: Run the formatter tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing_namespace.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Namespace the meta and clear-store paths**

Still in `dynamodbing.py`, update `_put_meta` (line 457), `_get_meta` (line 468), and `_clear_store` (line 485) so per-store metadata and clearing also respect the namespace:

```python
    def _put_meta(self, db: DynamoSubDb, meta: dict):
        """Store metadata for a subdb."""
        import json
        self._table.put_item(Item={
            "PK": f"__meta__#{self._nskey(db.name)}",
            "SK": _SK_META,
            "val": json.dumps(meta).encode("utf-8"),
            _GSI_PK: "__meta__",
            _GSI_SK: self._nskey(db.name),
        })

    def _get_meta(self, db: DynamoSubDb) -> dict | None:
        """Read metadata for a subdb."""
        import json
        resp = self._table.get_item(
            Key={"PK": f"__meta__#{self._nskey(db.name)}", "SK": _SK_META},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if not item or "val" not in item:
            return None
        raw = item["val"]
        if isinstance(raw, (bytes, memoryview)):
            raw = bytes(raw).decode("utf-8")
        elif not isinstance(raw, str):
            raw = bytes(raw).decode("utf-8")  # handles Binary wrapper
        return json.loads(raw)
```

In `_clear_store` (line 485), query the GSI by the namespaced store name and delete the namespaced meta entry:

```python
    def _clear_store(self, store_name: str):
        """Delete all items belonging to a store (within this namespace)."""
        kce = Key(_GSI_PK).eq(self._nskey(store_name))
        kwargs = {
            "IndexName": _GSI_NAME,
            "KeyConditionExpression": kce,
        }
        keys_to_delete = []
        while True:
            resp = self._table.query(**kwargs)
            for item in resp.get("Items", []):
                keys_to_delete.append({"PK": item["PK"], "SK": item["SK"]})
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek

        meta_key = {"PK": f"__meta__#{self._nskey(store_name)}", "SK": _SK_META}
        seen = {(k["PK"], k["SK"]) for k in keys_to_delete}
        if (meta_key["PK"], meta_key["SK"]) not in seen:
            keys_to_delete.append(meta_key)
```
(leave the batch-delete tail of the method unchanged)

- [ ] **Step 6: Write the moto isolation test**

Append to `tests/db/test_dynamodbing_namespace.py`:

```python
@needs_moto
def test_two_namespaces_in_one_table_are_isolated():
    """Same subdb + same key under two namespaces must not collide."""
    with mock_aws():
        kel = DynamoDBer.open(name="svc", stores=["kels."], region="us-east-1",
                              table_name="shared-core", namespace="rating:kel")
        tel = DynamoDBer.open(name="svc", stores=["kels."], region="us-east-1",
                              table_name="shared-core", namespace="rating:tel")
        ksub = kel.env.open_db(b"kels.")
        tsub = tel.env.open_db(b"kels.")
        kel.setVal(ksub, b"k", b"from-kel")
        tel.setVal(tsub, b"k", b"from-tel")
        assert kel.getVal(ksub, b"k") == b"from-kel"
        assert tel.getVal(tsub, b"k") == b"from-tel"  # not overwritten
        kel.close()
        tel.close()


@needs_moto
def test_legacy_namespace_still_isolated_from_namespaced():
    """An un-namespaced (legacy) instance shares no keys with a namespaced one."""
    with mock_aws():
        legacy = DynamoDBer.open(name="w", stores=["kels."], region="us-east-1",
                                 table_name="shared-core")
        ns = DynamoDBer.open(name="w", stores=["kels."], region="us-east-1",
                             table_name="shared-core", namespace="rating:kel")
        lsub = legacy.env.open_db(b"kels.")
        nsub = ns.env.open_db(b"kels.")
        legacy.setVal(lsub, b"k", b"legacy")
        assert ns.getVal(nsub, b"k") is None
        legacy.close()
        ns.close()
```

- [ ] **Step 7: Install `moto` into the worktree venv, then run all namespacing tests + the existing dynamo suite**

Run:
```bash
cd /Users/seriouscoderone/code/keripy/.worktrees/service-aid-framework
.venv/bin/pip install 'moto>=5.0'
.venv/bin/python -m pytest tests/db/test_dynamodbing_namespace.py tests/db/test_dynamodbing.py -v
```
Expected: PASS (4 new + existing dynamo tests green — the existing suite proves backward compatibility).

- [ ] **Step 8: Commit**

```bash
git add src/keri/db/dynamodbing.py tests/db/test_dynamodbing_namespace.py
git commit -m "feat(dynamodbing): optional per-tenant key namespacing (backward compatible)"
```

---

### Task 2: Framework package scaffold + config + Secrets-Manager `bran` loader

**Why:** Establish the installable `serviceaid` package, and the keeper-custody entry point (spec §7): a `bran` (passcode) loaded from Secrets Manager engages keripy's at-rest keeper encryption. Passing `bran` to `Habery` sets an `aeid` and re-encrypts private keys/salt (`keeping.py:769-778`, `habbing.py:266-275`).

**Files:**
- Create: `service-aid/pyproject.toml`, `service-aid/serviceaid/__init__.py`, `service-aid/serviceaid/config.py`
- Create: `service-aid/tests/__init__.py`, `service-aid/tests/conftest.py`, `service-aid/tests/test_config.py`

- [ ] **Step 1: Create the package metadata**

`service-aid/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=80"]
build-backend = "setuptools.build_meta"

[project]
name = "serviceaid"
version = "0.0.1"
description = "KERI Service AID serverless framework"
requires-python = ">=3.12"
dependencies = ["keri", "boto3>=1.34.0"]

[project.optional-dependencies]
dev = ["moto>=5.0", "pytest>=9.0.2"]
cdk = ["aws-cdk-lib>=2.140.0", "constructs>=10.0.0"]

[tool.setuptools.packages.find]
where = ["."]
include = ["serviceaid*"]
```

`service-aid/serviceaid/__init__.py`:

```python
"""KERI Service AID framework — wrap a Python function as an autonomous KERI principal."""
from .contract import service, Request, Reply  # noqa: F401

__all__ = ["service", "Request", "Reply"]
```
(Note: this import will fail until Task 3 creates `contract.py`. To keep Task 2 self-contained, temporarily make `__init__.py` empty and add the re-export in Task 3 Step 1.)

For Task 2, write `service-aid/serviceaid/__init__.py` as:

```python
"""KERI Service AID framework — wrap a Python function as an autonomous KERI principal."""
```

- [ ] **Step 2: Write the failing config test**

`service-aid/tests/_schema.py` (shared, importable by sibling test modules — do **not** add a `tests/__init__.py`, so pytest prepends `tests/` to `sys.path` and `from _schema import ...` resolves):

```python
"""Shared ACDC schema SAD for tests (saidified by callers)."""

RATING_SCHEMA_SAD = {
    "$id": "",
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "RatingResult",
    "type": "object",
    "properties": {
        "v": {"type": "string"},
        "d": {"type": "string"},
        "i": {"type": "string"},
        "ri": {"type": "string"},
        "s": {"type": "string"},
        "a": {
            "oneOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {
                        "d": {"type": "string"},
                        "i": {"type": "string"},
                        "dt": {"type": "string", "format": "date-time"},
                        "score": {"type": "number"},
                    },
                    "additionalProperties": False,
                    "required": ["d", "i", "dt", "score"],
                },
            ]
        },
    },
    "additionalProperties": False,
    "required": ["v", "d", "i", "ri", "s", "a"],
}
```

`service-aid/tests/test_config.py`:

```python
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from serviceaid.config import Config, load_bran

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("SERVICEAID_ALIAS", "rating")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "keri-core")
    monkeypatch.setenv("SERVICEAID_KEEPER_TABLE", "rating-ks")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "BWit1,BWit2")
    monkeypatch.setenv("SERVICEAID_TOAD", "2")
    monkeypatch.setenv("SERVICEAID_HANDLER", "rating_handler")
    monkeypatch.setenv("SERVICEAID_BRAN_SECRET", "rating/bran")
    monkeypatch.setenv("SERVICEAID_ALLOWLIST", "Ealice,Ebob")
    monkeypatch.setenv("SERVICEAID_REQUIRED_SCHEMA", "ESchemaReq")
    cfg = Config.from_env()
    assert cfg.alias == "rating"
    assert cfg.core_table == "keri-core"
    assert cfg.keeper_table == "rating-ks"
    assert cfg.witnesses == ["BWit1", "BWit2"]
    assert cfg.toad == 2
    assert cfg.handler_module == "rating_handler"
    assert cfg.bran_secret == "rating/bran"
    assert cfg.allowlist == ["Ealice", "Ebob"]
    assert cfg.required_schema == "ESchemaReq"
    assert cfg.kel_namespace == "rating:kel"
    assert cfg.tel_namespace == "rating:tel"


def test_config_toad_defaults_to_witness_count(monkeypatch):
    for k in ("SERVICEAID_TOAD",):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SERVICEAID_ALIAS", "r")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "c")
    monkeypatch.setenv("SERVICEAID_KEEPER_TABLE", "k")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "B1,B2,B3")
    monkeypatch.setenv("SERVICEAID_HANDLER", "h")
    cfg = Config.from_env()
    assert cfg.toad == 3


@needs_moto
def test_load_bran_from_secrets_manager():
    import boto3
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="a" * 21)
        assert load_bran("rating/bran", region="us-east-1") == "a" * 21
```

- [ ] **Step 3: Run to verify failure**

Run:
```bash
cd /Users/seriouscoderone/code/keripy/.worktrees/service-aid-framework
.venv/bin/pip install -e service-aid
.venv/bin/python -m pytest service-aid/tests/test_config.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'serviceaid.config'`.

- [ ] **Step 4: Implement `config.py`**

`service-aid/serviceaid/config.py`:

```python
"""Environment-driven configuration + Secrets-Manager bran loader."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    alias: str
    core_table: str
    keeper_table: str
    witnesses: list[str] = field(default_factory=list)
    toad: int = 0
    handler_module: str = ""
    bran_secret: str = ""
    allowlist: list[str] = field(default_factory=list)
    required_schema: str = ""
    region: str = "us-east-1"
    endpoint_url: str | None = None

    @property
    def kel_namespace(self) -> str:
        return f"{self.alias}:kel"

    @property
    def tel_namespace(self) -> str:
        return f"{self.alias}:tel"

    @classmethod
    def from_env(cls) -> "Config":
        wits = [w for w in os.environ.get("SERVICEAID_WITNESSES", "").split(",") if w]
        toad_env = os.environ.get("SERVICEAID_TOAD")
        toad = int(toad_env) if toad_env else len(wits)
        return cls(
            alias=os.environ["SERVICEAID_ALIAS"],
            core_table=os.environ["SERVICEAID_CORE_TABLE"],
            keeper_table=os.environ["SERVICEAID_KEEPER_TABLE"],
            witnesses=wits,
            toad=toad,
            handler_module=os.environ.get("SERVICEAID_HANDLER", ""),
            bran_secret=os.environ.get("SERVICEAID_BRAN_SECRET", ""),
            allowlist=[a for a in os.environ.get("SERVICEAID_ALLOWLIST", "").split(",") if a],
            required_schema=os.environ.get("SERVICEAID_REQUIRED_SCHEMA", ""),
            region=os.environ.get("SERVICEAID_REGION", "us-east-1"),
            endpoint_url=os.environ.get("SERVICEAID_ENDPOINT_URL") or None,
        )


def load_bran(secret_id: str, *, region: str = "us-east-1") -> str:
    """Fetch the keeper passcode (bran) from AWS Secrets Manager.

    The bran engages keripy's at-rest keeper encryption (aeid). It exists in
    plaintext only transiently in Lambda memory. Must be >= 21 chars.
    """
    import boto3
    sm = boto3.client("secretsmanager", region_name=region)
    resp = sm.get_secret_value(SecretId=secret_id)
    bran = resp["SecretString"]
    if len(bran) < 21:
        raise ValueError("bran (keeper passcode) must be at least 21 characters")
    return bran
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Add the shared `conftest.py`** (used by later tasks)

`service-aid/tests/conftest.py`:

```python
"""Shared fixtures: temp Habery, a saidified ACDC schema, recipient AID."""
import os
import tempfile

import pytest

os.environ.setdefault("HOME", tempfile.mkdtemp(prefix="serviceaid-test-"))

from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import scheming
from keri.kering import Kinds

from _schema import RATING_SCHEMA_SAD


@pytest.fixture
def issuer_hby():
    hby = Habery(name="svc", temp=True, salt=Salter(raw=b'0123456789abcdef').qb64)
    yield hby
    hby.close()


@pytest.fixture
def rating_schema(issuer_hby):
    """Saidify the schema, register it in the issuer's db, return (said, sad)."""
    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)
    issuer_hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    return schemer.said, schemer.sed


@pytest.fixture
def recipient_pre():
    """A deterministic recipient AID prefix for tests."""
    rcp_hby = Habery(name="rcp", temp=True, salt=Salter(raw=b'fedcba9876543210').qb64)
    hab = rcp_hby.makeHab(name="rcp", transferable=True)
    pre = hab.pre
    rcp_hby.close()
    return pre
```

- [ ] **Step 7: Commit**

```bash
git add service-aid/pyproject.toml service-aid/serviceaid/__init__.py service-aid/serviceaid/config.py service-aid/tests/
git commit -m "feat(serviceaid): package scaffold, env config, Secrets-Manager bran loader"
```

---

## Phase 1 — Framework core (Python)

### Task 3: Developer contract — `Request`, `Reply`, `@service.command`, `TestRuntime`

**Why:** The whole developer-facing surface (spec §5). A registry the handler routes against, plus a fake runtime so developers unit-test their function with zero keripy.

**Files:**
- Create: `service-aid/serviceaid/contract.py`, `service-aid/tests/test_contract.py`
- Modify: `service-aid/serviceaid/__init__.py` (re-export)

- [ ] **Step 1: Write the failing contract test**

`service-aid/tests/test_contract.py`:

```python
import pytest
from serviceaid.contract import Service, Request, Reply, TestRuntime


def test_command_registers_by_route():
    svc = Service()

    @svc.command(route="/rate/apply", issues="ESchemaSaid")
    def rate(req: Request) -> Reply:
        return Reply.acdc(recipient=req.sender, attributes={"score": 42})

    cmd = svc.lookup("/rate/apply")
    assert cmd is not None
    assert cmd.issues == "ESchemaSaid"
    assert cmd.fn is rate


def test_lookup_unknown_route_returns_none():
    assert Service().lookup("/nope") is None


def test_register_schema_returns_said_and_queues():
    svc = Service()
    sad = {"$id": "", "$schema": "http://json-schema.org/draft-07/schema#",
           "title": "T", "type": "object",
           "properties": {"a": {"type": "string"}}, "required": ["a"]}
    said = svc.register_schema(sad)
    assert said.startswith("E")          # SAID is a Blake3 digest
    assert len(svc.schemas) == 1
    assert svc.schemas[0]["$id"] == said  # saidified in place


def test_reply_constructors():
    r = Reply.acdc(recipient="Erecip", attributes={"score": 1}, edges={"x": "y"})
    assert r.kind == "acdc" and r.recipient == "Erecip"
    assert r.attributes == {"score": 1} and r.edges == {"x": "y"}
    assert Reply.none().kind == "none"
    assert Reply.reject(reason="nope").kind == "reject"
    assert Reply.reject(reason="nope").reason == "nope"


def test_request_now_is_iso8601():
    req = Request(sender="Eabc", payload={}, credentials=[],
                  message_said="EmsgX", payload_said="EpayX", route="/r")
    assert "T" in req.now() and req.now().endswith("+00:00")


def test_testruntime_dispatches_and_captures():
    svc = Service()

    @svc.command(route="/rate/apply", issues="ESchemaSaid")
    def rate(req: Request) -> Reply:
        return Reply.acdc(recipient=req.sender,
                          attributes={"score": req.payload["x"] * 2})

    rt = TestRuntime(svc)
    reply = rt.send(route="/rate/apply", sender="Ecaller", payload={"x": 21})
    assert reply.kind == "acdc"
    assert reply.attributes == {"score": 42}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serviceaid.contract'`.

- [ ] **Step 3: Implement `contract.py`**

`service-aid/serviceaid/contract.py`:

```python
"""Developer-facing contract: Service registry, Request, Reply, TestRuntime."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Request:
    """Verified, authorized inbound request handed to a developer function."""
    sender: str                       # verified caller AID prefix
    payload: dict                     # verified exn attributes (the `a` block)
    credentials: list = field(default_factory=list)  # verified attached ACDCs
    message_said: str = ""            # idempotency key (exn SAID)
    payload_said: str = ""            # SAID of the attributes block
    route: str = ""

    def now(self) -> str:
        from keri.help import helping
        return helping.nowIso8601()


@dataclass
class Reply:
    """Declarative reply. The framework performs issuance/signing/grant framing."""
    kind: str                         # "acdc" | "none" | "reject"
    recipient: Optional[str] = None
    attributes: Optional[dict] = None
    edges: Optional[dict] = None
    rules: Optional[dict] = None
    reason: Optional[str] = None

    @classmethod
    def acdc(cls, *, recipient: str, attributes: dict,
             edges: dict | None = None, rules: dict | None = None) -> "Reply":
        return cls(kind="acdc", recipient=recipient, attributes=attributes,
                   edges=edges, rules=rules)

    @classmethod
    def none(cls) -> "Reply":
        return cls(kind="none")

    @classmethod
    def reject(cls, *, reason: str) -> "Reply":
        return cls(kind="reject", reason=reason)


@dataclass
class Command:
    route: str
    issues: str            # ACDC schema SAID this command may issue
    fn: Callable[[Request], Reply]


class Service:
    """Registry populated by @service.command decorators at import time."""

    def __init__(self):
        self._commands: dict[str, Command] = {}
        self.schemas: list[dict] = []   # ACDC schema SADs to register at init

    def command(self, *, route: str, issues: str = ""):
        def deco(fn: Callable[[Request], Reply]):
            if route in self._commands:
                raise ValueError(f"duplicate route registered: {route}")
            self._commands[route] = Command(route=route, issues=issues, fn=fn)
            return fn
        return deco

    def register_schema(self, sad: dict) -> str:
        """Saidify an ACDC schema SAD, queue it for db registration, return its SAID.

        Called at developer-module import time so the runtime can load the schema
        into the Habery's schema store (required for credential issuance).
        """
        from keri.core import scheming
        from keri.kering import Kinds
        schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
        self.schemas.append(dict(schemer.sed))
        return schemer.said

    def lookup(self, route: str) -> Optional[Command]:
        return self._commands.get(route)

    @property
    def routes(self) -> list[str]:
        return list(self._commands)


# Module-level singleton imported by developer handler modules.
service = Service()


class TestRuntime:
    """In-memory runtime for unit-testing developer functions without keripy."""

    def __init__(self, svc: Service):
        self.svc = svc

    def send(self, *, route: str, sender: str, payload: dict,
             credentials: list | None = None) -> Reply:
        cmd = self.svc.lookup(route)
        if cmd is None:
            raise KeyError(f"no command for route {route}")
        req = Request(sender=sender, payload=payload,
                      credentials=credentials or [],
                      message_said="EtestMsg", payload_said="EtestPay",
                      route=route)
        return cmd.fn(req)
```

Update `service-aid/serviceaid/__init__.py`:

```python
"""KERI Service AID framework — wrap a Python function as an autonomous KERI principal."""
from .contract import service, Service, Request, Reply  # noqa: F401

__all__ = ["service", "Service", "Request", "Reply"]
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_contract.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add service-aid/serviceaid/contract.py service-aid/serviceaid/__init__.py service-aid/tests/test_contract.py
git commit -m "feat(serviceaid): developer contract (Request/Reply/@command) + TestRuntime"
```

---

### Task 4: Synchronous ACDC issuance + IPEX-grant framing (`issuing.py`)

**Why:** The reply path (spec §4, §6 step 6) and the first real exercise of `setup_reger` (spec §12.3). This is the highest-risk task, so it comes early and is proven end-to-end against a temp Habery. The sequence is adapted from the working synchronous path in the sibling Locksmith repo (`src/locksmith/core/credentialing.py:375-403` for issue, `src/locksmith/core/ipexing.py:70-107` for the grant), which runs against this same keripy fork.

**Files:**
- Create: `service-aid/serviceaid/issuing.py`, `service-aid/tests/test_issuing.py`

- [ ] **Step 1: Write the failing issuance test** (temp Habery + temp Regery, fast)

`service-aid/tests/test_issuing.py`:

```python
from keri.vdr import credentialing
from serviceaid.issuing import ensure_registry, issue_grant


def test_issue_grant_produces_verifiable_acdc(issuer_hby, rating_schema, recipient_pre):
    said, sad = rating_schema
    hab = issuer_hby.makeHab(name="svc", transferable=True)  # no wits in unit test
    rgy = credentialing.Regery(hby=issuer_hby, name="svc", temp=True)
    registry = ensure_registry(issuer_hby, hab, rgy, name="svc")

    grant = issue_grant(
        issuer_hby, hab, rgy,
        schema_said=said,
        recipient=recipient_pre,
        attributes={"score": 720},
    )

    # The grant is a CESR-framed IPEX /ipex/grant exn carrying the ACDC.
    assert isinstance(grant, (bytes, bytearray))
    assert b"/ipex/grant" in bytes(grant)

    # The credential was issued and saved in the registry.
    saiders = list(rgy.reger.schms.get(keys=(said,)))
    assert len(saiders) == 1
    creder = rgy.reger.creds.get(keys=(saiders[0].qb64,))
    assert creder is not None
    assert creder.attrib["score"] == 720
    assert creder.attrib["i"] == recipient_pre


def test_ensure_registry_is_idempotent(issuer_hby, rating_schema):
    hab = issuer_hby.makeHab(name="svc", transferable=True)
    rgy = credentialing.Regery(hby=issuer_hby, name="svc", temp=True)
    r1 = ensure_registry(issuer_hby, hab, rgy, name="svc")
    r2 = ensure_registry(issuer_hby, hab, rgy, name="svc")
    assert r1.regk == r2.regk
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_issuing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serviceaid.issuing'`.

- [ ] **Step 3: Implement `issuing.py`**

`service-aid/serviceaid/issuing.py`:

```python
"""Synchronous ACDC issuance + IPEX-grant framing for a Service AID.

Adapted from the proven synchronous path in the Locksmith wallet
(credentialing.py / ipexing.py), which runs against this keripy fork.
v1 uses a no-backer registry so TEL issuance needs no receipts and completes
in-process. The AID's KEL anchor is witnessed by the federation; collecting
those receipts is handled by the caller (runtime), not here.
"""
from __future__ import annotations

from keri.core import coring, eventing, signing, serdering
from keri.help import helping
from keri.vdr import credentialing, verifying
from keri.app import grouping
from keri.vc import protocoling


def ensure_registry(hby, hab, rgy, *, name: str):
    """Return the credential registry for `name`, creating it (no backers) if absent."""
    existing = rgy.registryByName(name)
    if existing is not None:
        return existing

    counselor = grouping.Counselor(hby=hby)
    registrar = credentialing.Registrar(hby=hby, rgy=rgy, counselor=counselor)

    registry = rgy.makeRegistry(name=name, prefix=hab.pre, noBackers=True)
    rseal = eventing.SealEvent(registry.regk, "0", registry.regd)
    rseal = dict(i=rseal.i, s=rseal.s, d=rseal.d)
    anc = hab.interact(data=[rseal])
    aserder = serdering.SerderKERI(raw=anc)
    registrar.incept(iserder=registry.vcp, anc=aserder)
    _complete(rgy, registrar, registry.regk, 0)
    return registry


def issue_grant(hby, hab, rgy, *, schema_said: str, recipient: str,
                attributes: dict, edges: dict | None = None,
                rules: dict | None = None, registry_name: str = "svc",
                message: str = "", timestamp: str | None = None) -> bytearray:
    """Issue an ACDC of `schema_said` to `recipient` and return a CESR IPEX grant."""
    timestamp = timestamp or helping.nowIso8601()
    registry = ensure_registry(hby, hab, rgy, name=registry_name)

    counselor = grouping.Counselor(hby=hby)
    registrar = credentialing.Registrar(hby=hby, rgy=rgy, counselor=counselor)
    verifier = verifying.Verifier(hby=hby, reger=rgy.reger)
    credentialer = credentialing.Credentialer(hby=hby, rgy=rgy,
                                               registrar=registrar, verifier=verifier)

    source = None
    if edges:
        source = dict(d="")
        for ename, edef in edges.items():
            source[ename] = {"n": edef["cred_said"], "s": edef["schema_said"]}
        _, source = coring.Saider.saidify(sad=source, label=coring.Saids.d)

    creder = credentialer.create(regname=registry_name, recp=recipient,
                                 schema=schema_said, source=source,
                                 rules=rules, data=attributes, private=False)

    dt = creder.attrib.get("dt", timestamp)
    iserder = registry.issue(said=creder.said, dt=dt)
    rseal = eventing.SealEvent(iserder.pre, iserder.snh, iserder.said)
    rseal = dict(i=rseal.i, s=rseal.s, d=rseal.d)
    anc = hab.interact(data=[rseal])
    aserder = serdering.SerderKERI(raw=anc)
    credentialer.issue(creder, iserder)
    registrar.issue(creder, iserder, aserder)
    _complete(rgy, registrar, registry.regk, iserder.sn)

    return _frame_grant(hby, hab, rgy, creder.said, recipient, message, timestamp)


def _frame_grant(hby, hab, rgy, said, recp, message, timestamp) -> bytearray:
    """Build a self-contained IPEX /ipex/grant exn carrying ACDC + iss + anchor."""
    creder, prefixer, seqner, saider = rgy.reger.cloneCred(said=said)
    acdc = signing.serialize(creder, prefixer, seqner, saider)

    iss = rgy.reger.cloneTvtAt(creder.said)
    iserder = serdering.SerderKERI(raw=bytes(iss))
    sq = coring.Seqner(sn=iserder.sn)
    serder = hby.db.fetchLastSealingEventByEventSeal(
        creder.sad["i"], seal=dict(i=iserder.pre, s=sq.snh, d=iserder.said))
    anc = hby.db.cloneEvtMsg(pre=serder.pre, fn=0, dig=serder.said)

    exn, atc = protocoling.ipexGrantExn(hab=hab, recp=recp, message=message,
                                        acdc=acdc, iss=iss, anc=anc, dt=timestamp)
    msg = bytearray(exn.raw)
    msg.extend(atc)
    return msg


def _complete(rgy, registrar, regk, sn, *, rounds: int = 20):
    """Drive escrow processing until the TEL event at (regk, sn) is complete.

    For a no-backer registry this converges immediately; the bounded loop is a
    safety net rather than a wait on network I/O. Escrow-processing entry points
    vary across keripy components, so each is called defensively.
    """
    def _pump():
        for obj, meth in ((registrar, "processEscrows"),
                          (rgy, "processEscrows"),
                          (getattr(rgy, "tvy", None), "processEscrows")):
            fn = getattr(obj, meth, None)
            if callable(fn):
                fn()

    for _ in range(rounds):
        if registrar.complete(pre=regk, sn=sn):
            return
        _pump()
    if not registrar.complete(pre=regk, sn=sn):
        raise RuntimeError(f"TEL event (regk={regk}, sn={sn}) did not complete")
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_issuing.py -v`
Expected: PASS (2 passed).

If `Registrar`/`Credentialer` constructor signatures differ in this fork, adjust to match `src/keri/vdr/credentialing.py` (`Registrar.__init__` ~line 502, `Credentialer.__init__` ~line 816). If a no-backer registry leaves an escrow pending, the bounded `_complete` loop surfaces it as a clear `RuntimeError` rather than a silent hang — debug with `keri.vdr.credentialing` in the systematic-debugging skill.

- [ ] **Step 5: Commit**

```bash
git add service-aid/serviceaid/issuing.py service-aid/tests/test_issuing.py
git commit -m "feat(serviceaid): synchronous ACDC issuance + IPEX-grant framing"
```

---

### Task 5: Authorization policy (`authorize.py`)

**Why:** Spec §9. After KERI verification (authenticity), confirm permission: sender allowlist and/or a required, verified credential.

**Files:**
- Create: `service-aid/serviceaid/authorize.py`, `service-aid/tests/test_authorize.py`

- [ ] **Step 1: Write the failing authorize test**

`service-aid/tests/test_authorize.py`:

```python
from serviceaid.authorize import Policy, authorize
from serviceaid.contract import Request


def _req(sender="Ecaller", creds=None):
    return Request(sender=sender, payload={}, credentials=creds or [],
                   message_said="m", payload_said="p", route="/r")


def test_no_policy_allows_all():
    ok, reason = authorize(_req(), Policy())
    assert ok and reason == ""


def test_allowlist_permits_listed_sender():
    ok, _ = authorize(_req(sender="Eok"), Policy(allowlist=["Eok"]))
    assert ok


def test_allowlist_rejects_unlisted_sender():
    ok, reason = authorize(_req(sender="Ebad"), Policy(allowlist=["Eok"]))
    assert not ok and "allowlist" in reason


def test_required_credential_present():
    creds = [{"schema": "ESchemaX", "issuer": "Eiss"}]
    ok, _ = authorize(_req(creds=creds), Policy(required_schema="ESchemaX"))
    assert ok


def test_required_credential_missing():
    ok, reason = authorize(_req(creds=[]), Policy(required_schema="ESchemaX"))
    assert not ok and "credential" in reason
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_authorize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serviceaid.authorize'`.

- [ ] **Step 3: Implement `authorize.py`**

`service-aid/serviceaid/authorize.py`:

```python
"""Authorization policy, evaluated after KERI verification."""
from __future__ import annotations

from dataclasses import dataclass, field

from .contract import Request


@dataclass
class Policy:
    allowlist: list[str] = field(default_factory=list)  # empty ⇒ any sender
    required_schema: str = ""                            # empty ⇒ none required


def authorize(req: Request, policy: Policy) -> tuple[bool, str]:
    """Return (allowed, reason). reason is empty when allowed."""
    if policy.allowlist and req.sender not in policy.allowlist:
        return False, f"sender {req.sender} not in allowlist"
    if policy.required_schema:
        present = any(c.get("schema") == policy.required_schema
                      for c in req.credentials)
        if not present:
            return False, f"missing required credential of schema {policy.required_schema}"
    return True, ""
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_authorize.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add service-aid/serviceaid/authorize.py service-aid/tests/test_authorize.py
git commit -m "feat(serviceaid): allowlist + required-credential authorization"
```

---

### Task 6: Idempotency store (`idempotency.py`)

**Why:** Spec §6 step 7, §10. Dedupe on the exn SAID so a duplicate delivery returns a cached ack without re-running or re-issuing.

**Files:**
- Create: `service-aid/serviceaid/idempotency.py`, `service-aid/tests/test_idempotency.py`

- [ ] **Step 1: Write the failing idempotency test** (moto-backed core table)

`service-aid/tests/test_idempotency.py`:

```python
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri.db.dynamodbing import DynamoDBer
from serviceaid.idempotency import Ledger, PROC_STORE

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


@needs_moto
def test_record_then_seen():
    with mock_aws():
        db = DynamoDBer.open(name="svc", stores=[PROC_STORE], region="us-east-1",
                             table_name="core", namespace="rating:proc")
        ledger = Ledger(db)
        assert ledger.seen("Emsg1") is None
        ledger.record("Emsg1", {"status": "ok", "credential": "Ecred1"})
        assert ledger.seen("Emsg1") == {"status": "ok", "credential": "Ecred1"}
        db.close()


@needs_moto
def test_unseen_message_returns_none():
    with mock_aws():
        db = DynamoDBer.open(name="svc", stores=[PROC_STORE], region="us-east-1",
                             table_name="core", namespace="rating:proc")
        assert Ledger(db).seen("nope") is None
        db.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_idempotency.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serviceaid.idempotency'`.

- [ ] **Step 3: Implement `idempotency.py`**

`service-aid/serviceaid/idempotency.py`:

```python
"""Exactly-once application of effects via an exn-SAID ledger on DynamoDB."""
from __future__ import annotations

import json

from keri.db import subing

PROC_STORE = "proc."


class Ledger:
    """Records processed exn SAIDs + a small effect summary on a DynamoDBer."""

    def __init__(self, db):
        # db is a DynamoDBer opened with PROC_STORE in its stores list.
        self.db = db
        self.proc = subing.Suber(db=db, subkey=PROC_STORE)

    def seen(self, said: str) -> dict | None:
        raw = self.proc.get(keys=(said,))
        if raw is None:
            return None
        return json.loads(raw)

    def record(self, said: str, summary: dict) -> None:
        self.proc.pin(keys=(said,), val=json.dumps(summary).encode("utf-8"))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_idempotency.py -v`
Expected: PASS (2 passed).

If `subing.Suber.get` returns a `str` rather than `bytes` in this fork, `json.loads` handles both; no change needed.

- [ ] **Step 5: Commit**

```bash
git add service-aid/serviceaid/idempotency.py service-aid/tests/test_idempotency.py
git commit -m "feat(serviceaid): idempotency ledger keyed on exn SAID"
```

---

### Task 7: Cold-start runtime (`runtime.py`) — Habery + AID + registry + handler import

**Why:** Spec §6 cold start. Generalizes `witness_handler.init` and `lambding.init`: open the three DynamoDBers (Baser `:kel`, Reger `:tel`, Keeper isolated/encrypted), build a `Habery` with the `bran`, load-or-incept the transferable+witnessed AID, build the Regery + registry, import the developer module, capture exns via a registered Exchanger handler. Warm singletons across invocations.

**Files:**
- Create: `service-aid/serviceaid/runtime.py`, `service-aid/tests/test_runtime.py`

- [ ] **Step 1: Write the failing runtime test** (moto core table + moto Secrets Manager; no witnesses so it incepts locally)

`service-aid/tests/test_runtime.py`:

```python
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from serviceaid.config import Config
from serviceaid import runtime

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def _cfg(**over):
    base = dict(alias="rating", core_table="keri-core", keeper_table="rating-ks",
                witnesses=[], toad=0, handler_module="", bran_secret="rating/bran",
                region="us-east-1", endpoint_url=None)
    base.update(over)
    return Config(**base)


@needs_moto
def test_init_incepts_transferable_aid_with_encrypted_keeper(monkeypatch):
    import boto3
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="x" * 21)

        runtime.reset()  # clear warm singletons between tests
        state = runtime.init(_cfg())

        # Transferable AID created.
        assert state.hab.pre.startswith("E") or state.hab.pre.startswith("D")
        assert state.hab.kever.transferable is True
        # Keeper encryption engaged (aeid set ⇒ private keys are ciphertext at rest).
        assert state.hby.ks.gbls.get("aeid") is not None
        # A credential registry exists for this service.
        assert state.rgy.registryByName("rating") is not None


@needs_moto
def test_init_is_warm_idempotent(monkeypatch):
    import boto3
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="x" * 21)
        runtime.reset()
        s1 = runtime.init(_cfg())
        s2 = runtime.init(_cfg())          # warm: returns the same singleton
        assert s1 is s2
        assert s1.hab.pre == s2.hab.pre
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_runtime.py -v`
Expected: FAIL — `AttributeError: module 'serviceaid.runtime' has no attribute 'reset'`.

- [ ] **Step 3: Implement `runtime.py`**

`service-aid/serviceaid/runtime.py`:

```python
"""Cold-start initialization and warm singletons for a Service AID Lambda."""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from keri.db.dynamodbing import DynamoDBer
from keri.app.lambding import (BASER_STORES, KEEPER_STORES, REGER_STORES,
                               setup_baser, setup_keeper, setup_reger)
from keri.app.habbing import Habery
from keri.app.configing import Configer
from keri.vdr import credentialing

from .config import Config, load_bran
from .contract import service, Service
from .authorize import Policy
from .issuing import ensure_registry
from .idempotency import Ledger, PROC_STORE

logger = logging.getLogger(__name__)

_state = None  # warm singleton across invocations


@dataclass
class RuntimeState:
    cfg: Config
    hby: Habery
    hab: object
    rgy: object
    ledger: Ledger
    svc: Service
    policy: Policy


def reset():
    """Drop the warm singleton (test/maintenance hook)."""
    global _state
    if _state is not None:
        try:
            _state.hby.close()
        except Exception:
            pass
    _state = None


class _CaptureHandler:
    """Exchanger behavior that stashes verified exns for synchronous dispatch."""

    def __init__(self, resource):
        self.resource = resource
        self.captured = []  # list of (serder, attachments)

    def verify(self, serder, attachments=None, **kw):
        return True

    def handle(self, serder, attachments=None, **kw):
        self.captured.append((serder, attachments or []))


def _dynamo_kwa(cfg: Config) -> dict:
    kwa = dict(region=cfg.region)
    if cfg.endpoint_url:
        kwa["endpoint_url"] = cfg.endpoint_url
        import boto3
        kwa["session"] = boto3.Session(aws_access_key_id="fake",
                                       aws_secret_access_key="fake",
                                       region_name=cfg.region)
    return kwa


def init(cfg: Config | None = None) -> RuntimeState:
    """Cold start: build Habery on DynamoDB, incept/load the AID + registry,
    import the developer handler. Warm invocations reuse the singleton."""
    global _state
    if _state is not None:
        return _state

    cfg = cfg or Config.from_env()
    kwa = _dynamo_kwa(cfg)

    # Baser (:kel) and Reger (:tel) share the pooled core table under distinct
    # namespaces — both define a `stts.` store, so they MUST be namespaced apart.
    db = DynamoDBer.open(name=cfg.alias, stores=BASER_STORES + [PROC_STORE],
                         table_name=cfg.core_table, namespace=cfg.kel_namespace, **kwa)
    setup_baser(db)
    reger = DynamoDBer.open(name=cfg.alias, stores=REGER_STORES,
                            table_name=cfg.core_table, namespace=cfg.tel_namespace, **kwa)
    setup_reger(reger)

    # Keeper: isolated table, encrypted via bran (aeid). Never pooled.
    ks = DynamoDBer.open(name=f"{cfg.alias}-ks", stores=KEEPER_STORES,
                         table_name=cfg.keeper_table, **kwa)
    setup_keeper(ks)

    bran = load_bran(cfg.bran_secret, region=cfg.region) if cfg.bran_secret else None

    cf = Configer(name=cfg.alias, temp=True)  # Lambda: filesystem only in /tmp
    hby = Habery(name=cfg.alias, temp=False, free=True, db=db, ks=ks, cf=cf, bran=bran)

    hab = hby.habByName(cfg.alias)
    if hab is None:
        hab = hby.makeHab(name=cfg.alias, transferable=True,
                          wits=cfg.witnesses, toad=cfg.toad,
                          isith="1", icount=1, nsith="1", ncount=1)
    hby.prefixes.add(hab.pre)

    rgy = credentialing.Regery(hby=hby, name=cfg.alias, reger=reger)
    ensure_registry(hby, hab, rgy, name=cfg.alias)

    svc = service
    if cfg.handler_module:
        importlib.import_module(cfg.handler_module)  # decorators populate `service`
    # Register the developer's ACDC schemas so Credentialer.create can validate.
    from keri.core import scheming
    from keri.kering import Kinds
    for sad in svc.schemas:
        schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
        if hby.db.schema.get(keys=(schemer.said,)) is None:
            hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    for route in svc.routes:
        hby.exc.addHandler(_CaptureHandler(resource=route))

    policy = Policy(allowlist=cfg.allowlist, required_schema=cfg.required_schema)
    _state = RuntimeState(cfg=cfg, hby=hby, hab=hab, rgy=rgy,
                          ledger=Ledger(db), svc=svc, policy=policy)
    return _state
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_runtime.py -v`
Expected: PASS (2 passed).

If `makeHab(wits=[], toad=0, ...)` complains in this fork, check `Habery.makeHab` (`habbing.py:365`) for the exact non-witnessed signature; an empty `wits` list with `toad=0` must incept locally without network. If `Habery(bran=...)` requires the bran via `setup`, confirm `__init__` forwards `**kwa` to `setup` (it does — `habbing.py:207-211`).

- [ ] **Step 5: Commit**

```bash
git add service-aid/serviceaid/runtime.py service-aid/tests/test_runtime.py
git commit -m "feat(serviceaid): cold-start runtime (Habery+AID+registry, encrypted keeper, namespaced pool)"
```

---

### Task 8: Lambda handler (`handler.py`) — verify → authorize → dispatch → reply, end-to-end

**Why:** Spec §6 per-request flow and §10 error table. The generic Lambda entry point that ties the framework together, proven end-to-end: a real self-contained-CESR request goes through full KERI verification and the issued ACDC in the grant verifies in a fresh consumer Habery.

**Files:**
- Create: `service-aid/serviceaid/handler.py`, `service-aid/tests/test_handler_e2e.py`

- [ ] **Step 1: Write the failing end-to-end handler test**

`service-aid/tests/test_handler_e2e.py`:

```python
import base64
import json

import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import scheming
from keri.kering import Kinds
from keri.peer import exchanging

from serviceaid import runtime
from serviceaid.config import Config
from serviceaid.contract import service, Reply
from _schema import RATING_SCHEMA_SAD

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def _cfg(**o):
    b = dict(alias="rating", core_table="keri-core", keeper_table="rating-ks",
             witnesses=[], toad=0, handler_module="", bran_secret="rating/bran",
             region="us-east-1", endpoint_url=None)
    b.update(o)
    return Config(**b)


@needs_moto
def test_full_request_returns_verifiable_grant(monkeypatch):
    import boto3
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="z" * 21)

        # Register a command + the schema BEFORE init (handler_module="" ⇒ inline).
        runtime.reset()
        service._commands.clear()
        service.schemas.clear()
        schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)
        said = schemer.said

        @service.command(route="/rate/apply", issues=said)
        def rate(req):
            return Reply.acdc(recipient=req.sender,
                              attributes={"score": req.payload["risk"] * 10})

        state = runtime.init(_cfg())
        state.hby.db.schema.pin(keys=(said,), val=schemer)

        from serviceaid import handler as H

        # Caller builds a self-contained CESR exn: their KEL + a signed /rate/apply exn.
        caller_hby = Habery(name="caller", temp=True, salt=Salter(raw=b'caller9876543210').qb64)
        caller = caller_hby.makeHab(name="caller", transferable=True)
        exn, _ = exchanging.exchange(route="/rate/apply",
                                     attributes={"risk": 72}, sender=caller.pre)
        # Sign + assemble: caller's KEL (so the service can verify) + exn + sigs.
        ims = bytearray(caller.makeOwnEvent(sn=0))     # caller inception (KEL)
        ims.extend(caller.endorse(exn, last=False))    # exn + attached signatures

        event = {"path": "/rate/apply", "httpMethod": "POST",
                 "body": base64.b64encode(bytes(ims)).decode(), "isBase64Encoded": True}
        resp = H.handler(event, None)

        assert resp["statusCode"] == 200
        assert resp["headers"]["Content-Type"] == "application/cesr"
        grant = resp["body"].encode("utf-8")
        assert b"/ipex/grant" in grant

        # Consumer verifies the issued ACDC end-to-end by parsing the grant.
        consumer = Habery(name="consumer", temp=True, salt=Salter(raw=b'consumer87654321').qb64)
        # Resolve the issuer KEL so the grant's ACDC verifies.
        consumer.psr.parse(ims=bytearray(state.hby.db.cloneEvtMsg(
            pre=state.hab.pre, fn=0, dig=state.hab.kever.serder.said)))
        consumer.psr.parse(ims=bytearray(grant))
        caller_hby.close(); consumer.close()


@needs_moto
def test_duplicate_message_is_idempotent(monkeypatch):
    import boto3
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="z" * 21)
        runtime.reset()
        service._commands.clear()
        service.schemas.clear()
        schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)

        @service.command(route="/rate/apply", issues=schemer.said)
        def rate(req):
            return Reply.acdc(recipient=req.sender, attributes={"score": 1})

        state = runtime.init(_cfg())
        state.hby.db.schema.pin(keys=(schemer.said,), val=schemer)
        from serviceaid import handler as H

        caller_hby = Habery(name="caller", temp=True, salt=Salter(raw=b'caller9876543210').qb64)
        caller = caller_hby.makeHab(name="caller", transferable=True)
        exn, _ = exchanging.exchange(route="/rate/apply", attributes={"risk": 5}, sender=caller.pre)
        ims = bytearray(caller.makeOwnEvent(sn=0)); ims.extend(caller.endorse(exn, last=False))
        event = {"path": "/rate/apply", "httpMethod": "POST",
                 "body": base64.b64encode(bytes(ims)).decode(), "isBase64Encoded": True}

        r1 = H.handler(event, None)
        n_creds_after_first = len(list(state.rgy.reger.creds.getTopItemIter()))
        r2 = H.handler(event, None)              # duplicate exn SAID
        n_creds_after_second = len(list(state.rgy.reger.creds.getTopItemIter()))
        assert r1["statusCode"] == 200 and r2["statusCode"] == 200
        assert n_creds_after_first == n_creds_after_second   # no re-issue
        caller_hby.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_handler_e2e.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serviceaid.handler'`.

- [ ] **Step 3: Implement `handler.py`**

`service-aid/serviceaid/handler.py`:

```python
"""Generic Service AID Lambda entry point: verify → authorize → dispatch → reply."""
from __future__ import annotations

import base64
import json
import logging

from . import runtime
from .authorize import authorize
from .contract import Request, Reply
from .issuing import issue_grant

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _body_bytes(event) -> bytes:
    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode("utf-8") if isinstance(body, str) else bytes(body)


def _cesr_response(status, body):
    if body is None:
        return {"statusCode": status}
    return {"statusCode": status,
            "headers": {"Content-Type": "application/cesr"},
            "body": bytes(body).decode("utf-8")}


def _json_response(status, obj):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(obj)}


def handler(event, context):
    # CloudFormation Custom Resource events (inception) share this Lambda.
    # They carry RequestType instead of httpMethod — delegate before HTTP routing.
    if "RequestType" in event:
        from .cdk.inception import on_event
        return on_event(event, context)

    state = runtime.init()
    method = event.get("httpMethod", "GET")
    path = (event.get("path", "/") or "/").rstrip("/") or "/"

    if method == "GET" and path == "/":
        return _json_response(200, {"service": state.hab.pre,
                                    "alias": state.cfg.alias,
                                    "routes": state.svc.routes})

    cmd = state.svc.lookup(path)
    if cmd is None:
        return _json_response(404, {"error": f"no command for route {path}"})

    ims = _body_bytes(event)
    if not ims:
        return _json_response(400, {"error": "empty body"})

    # Drain any prior captures for this route's handler, then parse.
    behavior = state.hby.exc.routes.get(path)
    behavior.captured.clear()
    try:
        state.hby.psr.parse(ims=bytearray(ims), framed=True)
        state.hby.kvy.processEscrows()
        state.hby.exc.processEscrow()
    except Exception as exc:  # verification failure ⇒ cannot sign a KERI reply
        logger.warning("verification failed on %s: %s", path, exc, exc_info=True)
        return _json_response(400, {"error": "verification failed"})

    if not behavior.captured:
        return _json_response(400, {"error": "no verified exn for route"})

    serder, attachments = behavior.captured[-1]

    # Idempotency: a duplicate exn SAID short-circuits before dispatch.
    cached = state.ledger.seen(serder.said)
    if cached is not None:
        return _json_response(200, {"status": "duplicate", **cached})

    attrs = serder.ked.get("a", {}) or {}
    req = Request(sender=serder.ked["i"], payload=attrs, credentials=[],
                  message_said=serder.said,
                  payload_said=attrs.get("d", "") if isinstance(attrs, dict) else "",
                  route=path)

    ok, reason = authorize(req, state.policy)
    if not ok:
        logger.info("authorization denied on %s: %s", path, reason)
        return _json_response(403, {"error": "forbidden", "reason": reason})

    try:
        reply = cmd.fn(req)
    except Exception as exc:           # handler raised ⇒ retry-safe, not recorded
        logger.error("handler raised on %s: %s", path, exc, exc_info=True)
        return _json_response(500, {"error": "handler error"})

    if reply.kind == "none":
        state.ledger.record(serder.said, {"status": "ok"})
        return _cesr_response(204, None)
    if reply.kind == "reject":
        return _json_response(403, {"error": "rejected", "reason": reply.reason})

    grant = issue_grant(state.hby, state.hab, state.rgy,
                        schema_said=cmd.issues, recipient=reply.recipient,
                        attributes=reply.attributes, edges=reply.edges,
                        rules=reply.rules, registry_name=state.cfg.alias)
    state.ledger.record(serder.said, {"status": "ok"})
    return _cesr_response(200, grant)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_handler_e2e.py -v`
Expected: PASS (2 passed).

This is the riskiest integration point. If the caller-side assembly (`makeOwnEvent` / `endorse` / `exchanging.exchange` — note `attributes=`, not `payload=`; it returns `(serder, end)`) differs in this fork, mirror exactly how `sam-witness/test_live.py` builds and submits self-contained CESR, and how Locksmith builds an `exn` in `src/locksmith/core/ipexing.py`. Use the systematic-debugging skill; do not weaken the assertion that the grant's ACDC verifies in a fresh consumer Habery — that is the proof the whole pipeline works.

- [ ] **Step 5: Run the whole framework suite + the keripy namespacing test together**

Run:
```bash
.venv/bin/python -m pytest service-aid/tests/ tests/db/test_dynamodbing_namespace.py -v
```
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add service-aid/serviceaid/handler.py service-aid/tests/test_handler_e2e.py
git commit -m "feat(serviceaid): generic Lambda handler — verify/authorize/dispatch/reply + idempotency"
```

---

## Phase 2 — Container image + CDK deployment

### Task 9: Container image (`Dockerfile`, `bootstrap.py`, `requirements.txt`)

**Why:** Spec §4. The Lambda runs as a container image (mirrors `sam-witness/Dockerfile`), bundling keripy + the framework + the developer handler module, with the libsodium shim.

**Files:**
- Create: `service-aid/bootstrap.py`, `service-aid/Dockerfile`, `service-aid/requirements.txt`

- [ ] **Step 1: Write the bootstrap import test** (verifies the shim module imports the handler)

`service-aid/tests/test_bootstrap.py`:

```python
def test_bootstrap_exposes_handler():
    import importlib, sys, pathlib
    root = pathlib.Path(__file__).resolve().parents[1]   # service-aid/
    sys.path.insert(0, str(root))
    mod = importlib.import_module("bootstrap")
    assert callable(mod.handler)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_bootstrap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap'`.

- [ ] **Step 3: Create `bootstrap.py`** (mirrors `sam-witness/bootstrap.py:1-37`)

`service-aid/bootstrap.py`:

```python
"""Lambda bootstrap: load libsodium before keri imports, then expose handler."""
import ctypes
import ctypes.util
import os

_task_dir = os.path.dirname(os.path.abspath(__file__))
_candidates = [
    os.path.join(_task_dir, "lib", "libsodium.so.26"),
    os.path.join(_task_dir, "lib", "libsodium.so"),
    os.path.join(_task_dir, "libsodium.so.26"),
    os.path.join(_task_dir, "libsodium.so"),
]
_lib_path = next((p for p in _candidates if os.path.exists(p)), None)
if _lib_path:
    _orig = ctypes.util.find_library

    def _patched(name):
        return _lib_path if name in ("sodium", "libsodium") else _orig(name)

    ctypes.util.find_library = _patched

from serviceaid.handler import handler  # noqa: E402,F401
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_bootstrap.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Create `requirements.txt`** (copy `sam-witness/requirements.txt` content verbatim — same runtime deps)

`service-aid/requirements.txt`:

```
lmdb>=2.1.1
pysodium>=0.7.18
blake3>=1.0.8
msgpack>=1.1.2
cbor2>=5.8.0
multidict>=6.7.0
ordered-set>=4.1.0
hio>=0.7.19
multicommand==1.0.0
jsonschema>=4.26.0
falcon>=4.2.0
hjson>=3.1.0
PyYaml>=6.0.3
apispec>=6.9.0
mnemonic>=0.21
PrettyTable>=3.17.0
http_sfv>=0.9.9
cryptography>=46.0.3
semver>=3.0.4
sortedcontainers>=2.4.0
boto3>=1.34.0
```

- [ ] **Step 6: Create `Dockerfile`** (mirrors `sam-witness/Dockerfile`, but copies the `serviceaid` package and the developer handler dir)

`service-aid/Dockerfile`:

```dockerfile
FROM python:3.14-slim AS build-stage

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsodium23 libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY service-aid/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /deps/

COPY src/keri /deps/keri
COPY service-aid/serviceaid /deps/serviceaid
COPY service-aid/bootstrap.py /deps/bootstrap.py
# Developer handler module mounted at build time (see ServiceAid construct):
COPY service-aid/examples/rating_engine/handler.py /deps/rating_handler.py

RUN mkdir -p /deps/lib && find /usr/lib -name 'libsodium.so*' -exec cp -P {} /deps/lib/ \;

FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsodium23 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir awslambdaric

WORKDIR /var/task
COPY --from=build-stage /deps/ /var/task/

ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["bootstrap.handler"]
```

- [ ] **Step 7: Commit**

```bash
git add service-aid/bootstrap.py service-aid/Dockerfile service-aid/requirements.txt service-aid/tests/test_bootstrap.py
git commit -m "feat(serviceaid): container image (Dockerfile + libsodium bootstrap)"
```

---

### Task 10: Shared `KeriCoreStack` (pooled table)

**Why:** Spec §3. One DynamoDB table per account/environment holding Tier-1 public KERI state for all services, exported via SSM so per-service stacks reference it.

**Files:**
- Create: `service-aid/serviceaid/cdk/__init__.py`, `service-aid/serviceaid/cdk/keri_core_stack.py`, `service-aid/tests/test_cdk_synth.py`

- [ ] **Step 1: Write the failing synth test** (CDK assertions — no AWS calls)

`service-aid/tests/test_cdk_synth.py`:

```python
import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk import App
from aws_cdk.assertions import Template
from serviceaid.cdk.keri_core_stack import KeriCoreStack


def test_core_stack_creates_pooled_table_and_ssm_export():
    app = App()
    stack = KeriCoreStack(app, "KeriCore", table_name="keri-core")
    t = Template.from_stack(stack)
    t.resource_count_is("AWS::DynamoDB::Table", 1)
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "TableName": "keri-core",
        "BillingMode": "PAY_PER_REQUEST",
    })
    t.resource_count_is("AWS::SSM::Parameter", 1)
```

- [ ] **Step 2: Install CDK deps + run to verify failure**

Run:
```bash
.venv/bin/pip install 'aws-cdk-lib>=2.140.0' 'constructs>=10.0.0'
.venv/bin/python -m pytest service-aid/tests/test_cdk_synth.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'serviceaid.cdk'`.

- [ ] **Step 3: Implement the CDK package + core stack**

`service-aid/serviceaid/cdk/__init__.py`:

```python
"""CDK constructs for KERI Service AIDs."""
from .keri_core_stack import KeriCoreStack  # noqa: F401
from .service_aid_construct import ServiceAid  # noqa: F401

__all__ = ["KeriCoreStack", "ServiceAid"]
```
(Note: the `ServiceAid` import fails until Task 12; for Task 10, set `__init__.py` to import only `KeriCoreStack`, then add `ServiceAid` in Task 12 Step 3.)

For Task 10, `service-aid/serviceaid/cdk/__init__.py`:

```python
"""CDK constructs for KERI Service AIDs."""
from .keri_core_stack import KeriCoreStack  # noqa: F401

__all__ = ["KeriCoreStack"]
```

`service-aid/serviceaid/cdk/keri_core_stack.py`:

```python
"""Shared KeriCoreStack: one pooled DynamoDB table for all services' public state."""
from aws_cdk import Stack, RemovalPolicy, CfnOutput
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ssm as ssm
from constructs import Construct

CORE_TABLE_SSM = "/serviceaid/core-table-name"
GSI_NAME = "subdb-index"


class KeriCoreStack(Stack):
    """Pooled Tier-1 KERI-state table (KEL/Baser + TEL/Reger), namespaced per service."""

    def __init__(self, scope: Construct, cid: str, *, table_name: str = "keri-core", **kw):
        super().__init__(scope, cid, **kw)

        self.table = ddb.Table(
            self, "CoreTable",
            table_name=table_name,
            partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.table.add_global_secondary_index(
            index_name=GSI_NAME,
            partition_key=ddb.Attribute(name="gsi_pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="gsi_sk", type=ddb.AttributeType.STRING),
        )

        ssm.StringParameter(self, "CoreTableNameParam",
                            parameter_name=CORE_TABLE_SSM,
                            string_value=self.table.table_name)
        CfnOutput(self, "CoreTableName", value=self.table.table_name)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_cdk_synth.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Add CDK deps to `pyproject.toml` dev extras** (already present in Task 2; verify `aws-cdk-lib` + `constructs` are listed under `[project.optional-dependencies] cdk`).

- [ ] **Step 6: Commit**

```bash
git add service-aid/serviceaid/cdk/__init__.py service-aid/serviceaid/cdk/keri_core_stack.py service-aid/tests/test_cdk_synth.py
git commit -m "feat(serviceaid): shared KeriCoreStack (pooled DynamoDB table + SSM export)"
```

---

### Task 11: Inception Custom Resource handler (`inception.py`)

**Why:** Spec §6, §2.6. On stack create, incept the AID + registry exactly once (so the AID exists before the first request), idempotent across CloudFormation retries.

**Files:**
- Create: `service-aid/serviceaid/cdk/inception.py`, `service-aid/tests/test_inception.py`

- [ ] **Step 1: Write the failing inception test** (moto core table + Secrets Manager)

`service-aid/tests/test_inception.py`:

```python
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


@needs_moto
def test_on_create_incepts_and_returns_pre(monkeypatch):
    import boto3
    from serviceaid import runtime
    from serviceaid.cdk import inception
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="q" * 21)
        runtime.reset()
        env = {"SERVICEAID_ALIAS": "rating", "SERVICEAID_CORE_TABLE": "keri-core",
               "SERVICEAID_KEEPER_TABLE": "rating-ks", "SERVICEAID_WITNESSES": "",
               "SERVICEAID_HANDLER": "", "SERVICEAID_BRAN_SECRET": "rating/bran"}
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        event = {"RequestType": "Create"}
        result = inception.on_event(event, None)
        assert result["PhysicalResourceId"].startswith(("E", "D"))
        assert result["Data"]["ServiceAidPre"] == result["PhysicalResourceId"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_inception.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serviceaid.cdk.inception'`.

- [ ] **Step 3: Implement `inception.py`**

`service-aid/serviceaid/cdk/inception.py`:

```python
"""CloudFormation Custom Resource: incept the Service AID + registry on create.

Idempotent — runtime.init() load-or-incepts, so CFN retries are safe. Delete
is a no-op (the AID/keys persist; teardown of data is an operator decision)."""
from __future__ import annotations

import logging

from serviceaid import runtime

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def on_event(event, context):
    request_type = event.get("RequestType", "Create")
    if request_type in ("Create", "Update"):
        runtime.reset()
        state = runtime.init()      # reads config from env; incepts if absent
        pre = state.hab.pre
        logger.info("Service AID inception complete: alias=%s pre=%s",
                    state.cfg.alias, pre)
        return {"PhysicalResourceId": pre, "Data": {"ServiceAidPre": pre}}

    # Delete: keep the AID and its keys; nothing to undo.
    return {"PhysicalResourceId": event.get("PhysicalResourceId", "noop")}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_inception.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add service-aid/serviceaid/cdk/inception.py service-aid/tests/test_inception.py
git commit -m "feat(serviceaid): inception Custom Resource handler (idempotent AID+registry)"
```

---

### Task 12: `ServiceAid` construct

**Why:** Spec §3. The per-service thin stack: container Lambda + API Gateway + IAM scoped to the service's namespace prefix in the shared table + isolated keeper table + bran secret + inception Custom Resource.

**Files:**
- Create: `service-aid/serviceaid/cdk/service_aid_construct.py`
- Modify: `service-aid/serviceaid/cdk/__init__.py`, `service-aid/tests/test_cdk_synth.py`

- [ ] **Step 1: Append the failing construct synth test**

Append to `service-aid/tests/test_cdk_synth.py`:

```python
def test_service_aid_construct_provisions_lambda_apigw_keeper():
    from aws_cdk import App, Stack
    from aws_cdk.assertions import Template
    from serviceaid.cdk.service_aid_construct import ServiceAid

    app = App()
    stack = Stack(app, "RatingSvc")
    ServiceAid(stack, "Rating",
               alias="rating", core_table_name="keri-core",
               handler_module="rating_handler",
               witnesses=["BWit1", "BWit2"], toad=2,
               image_directory=".")  # synth-only; image not built in test
    t = Template.from_stack(stack)
    # cr.Provider synthesizes its own framework Lambda(s), so don't count
    # functions — assert the service function exists by name/env instead.
    t.resource_count_is("AWS::DynamoDB::Table", 1)         # isolated keeper table
    t.resource_count_is("AWS::SecretsManager::Secret", 1)  # keeper bran
    t.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "rating-serviceaid",
        "Environment": {"Variables": {"SERVICEAID_ALIAS": "rating"}}
    })
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_cdk_synth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serviceaid.cdk.service_aid_construct'`.

- [ ] **Step 3: Implement the construct + export it**

`service-aid/serviceaid/cdk/service_aid_construct.py`:

```python
"""ServiceAid construct: per-service Lambda + API Gateway + scoped IAM + keeper."""
from aws_cdk import Duration, CustomResource
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_iam as iam
from aws_cdk import custom_resources as cr
from constructs import Construct


class ServiceAid(Construct):
    """One Service AID: container Lambda over the shared core table (own namespace)
    + an isolated, encrypted keeper table + bran secret + inception Custom Resource."""

    def __init__(self, scope: Construct, cid: str, *, alias: str,
                 core_table_name: str, handler_module: str,
                 witnesses: list[str] | None = None, toad: int = 0,
                 image_directory: str = ".", memory: int = 1024,
                 timeout_seconds: int = 120, **kw):
        super().__init__(scope, cid, **kw)
        witnesses = witnesses or []

        core_table = ddb.Table.from_table_name(self, "CoreTable", core_table_name)

        # Tier-2: isolated, encrypted keeper table (never pooled).
        keeper_table = ddb.Table(
            self, "KeeperTable",
            table_name=f"{alias}-ks",
            partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=ddb.TableEncryption.AWS_MANAGED,
        )
        keeper_table.add_global_secondary_index(
            index_name="subdb-index",
            partition_key=ddb.Attribute(name="gsi_pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="gsi_sk", type=ddb.AttributeType.STRING),
        )

        # Keeper passcode (bran) — generated once, never logged.
        bran = sm.Secret(
            self, "KeeperBran",
            secret_name=f"{alias}/bran",
            generate_secret_string=sm.SecretStringGenerator(
                password_length=32, exclude_punctuation=True),
        )

        env = {
            "SERVICEAID_ALIAS": alias,
            "SERVICEAID_CORE_TABLE": core_table_name,
            "SERVICEAID_KEEPER_TABLE": keeper_table.table_name,
            "SERVICEAID_WITNESSES": ",".join(witnesses),
            "SERVICEAID_TOAD": str(toad),
            "SERVICEAID_HANDLER": handler_module,
            "SERVICEAID_BRAN_SECRET": bran.secret_name,
            "SERVICEAID_REGION": self.node.try_get_context("region") or "us-east-1",
            "LD_LIBRARY_PATH": "/var/task/lib",
        }

        fn = _lambda.DockerImageFunction(
            self, "Function",
            function_name=f"{alias}-serviceaid",
            code=_lambda.DockerImageCode.from_image_asset(image_directory),
            memory_size=memory,
            timeout=Duration.seconds(timeout_seconds),
            architecture=_lambda.Architecture.ARM_64,
            environment=env,
        )

        # IAM: keeper full CRUD; core table scoped to this service's namespace prefix.
        keeper_table.grant_read_write_data(fn)
        bran.grant_read(fn)
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem",
                     "dynamodb:Query", "dynamodb:BatchWriteItem"],
            resources=[core_table.table_arn, f"{core_table.table_arn}/index/*"],
            conditions={"ForAllValues:StringLike": {
                "dynamodb:LeadingKeys": [f"{alias}:*#*", f"__meta__#{alias}:*"]}},
        ))

        api = apigw.LambdaRestApi(
            self, "Api", handler=fn, proxy=True,
            binary_media_types=["application/cesr", "*/*"],
        )
        self.api = api
        self.function = fn

        # Inception Custom Resource: incept AID + registry once on create.
        provider = cr.Provider(self, "InceptionProvider", on_event_handler=fn)
        self.inception = CustomResource(
            self, "Inception", service_token=provider.service_token,
            properties={"Alias": alias},
        )
```

Update `service-aid/serviceaid/cdk/__init__.py` to export both:

```python
"""CDK constructs for KERI Service AIDs."""
from .keri_core_stack import KeriCoreStack  # noqa: F401
from .service_aid_construct import ServiceAid  # noqa: F401

__all__ = ["KeriCoreStack", "ServiceAid"]
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_cdk_synth.py -v`
Expected: PASS (2 passed).

The inception Custom Resource reuses the service function as its `on_event_handler`. This works because `serviceaid.handler.handler` (Task 8) routes by event shape: events carrying `RequestType` (CloudFormation) are delegated to `inception.on_event` before any HTTP routing. Note that `cr.Provider` synthesizes its own framework Lambda, so the synth test's count of 1 refers to functions with our container image; if the assertion fails on the provider's extra function, match on `FunctionName: "rating-serviceaid"` instead of counting.

- [ ] **Step 5: Commit**

```bash
git add service-aid/serviceaid/cdk/service_aid_construct.py service-aid/serviceaid/cdk/__init__.py service-aid/tests/test_cdk_synth.py
git commit -m "feat(serviceaid): ServiceAid construct (Lambda+APIGW+scoped IAM+keeper+inception CR)"
```

---

## Phase 3 — Reference example

### Task 13: Rating Engine example (handler + schema + CDK app)

**Why:** Spec §13 (one reference example). Proves the developer experience: a single function + a CDK app instantiating `KeriCoreStack` + `ServiceAid`.

**Files:**
- Create: `service-aid/examples/rating_engine/handler.py`, `service-aid/examples/rating_engine/schema/rating_result.json`, `service-aid/examples/rating_engine/app.py`, `service-aid/tests/test_example_rating.py`

- [ ] **Step 1: Write the failing example test** (unit-tests the developer function via TestRuntime)

`service-aid/tests/test_example_rating.py`:

```python
import importlib
import sys
import pathlib

from serviceaid.contract import service, TestRuntime


def test_rating_engine_scores_via_testruntime():
    service._commands.clear()
    service.schemas.clear()
    root = pathlib.Path(__file__).resolve().parents[1] / "examples" / "rating_engine"
    sys.path.insert(0, str(root))
    # Decorators run only on first import; reload if the module is cached.
    mod = sys.modules.get("handler")
    importlib.reload(mod) if mod else importlib.import_module("handler")

    rt = TestRuntime(service)
    reply = rt.send(route="/rate/apply", sender="Ecaller",
                    payload={"risk_profile": {"age": 30, "claims": 0}})
    assert reply.kind == "acdc"
    assert isinstance(reply.attributes["score"], (int, float))
    assert reply.recipient == "Ecaller"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest service-aid/tests/test_example_rating.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handler'`.

- [ ] **Step 3: Implement the example handler**

`service-aid/examples/rating_engine/handler.py`:

```python
"""Reference Service AID: a trivial rating engine."""
import json
import pathlib

from serviceaid import service, Request, Reply

# Compute the real schema SAID from the bundled schema and queue it for the
# runtime to register into the Habery's schema store at init.
_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema" / "rating_result.json"
RATING_SCHEMA_SAID = service.register_schema(json.loads(_SCHEMA_PATH.read_text()))


def _score(profile: dict) -> int:
    base = 800
    base -= int(profile.get("age", 0) < 25) * 50
    base -= int(profile.get("claims", 0)) * 40
    return max(300, min(850, base))


@service.command(route="/rate/apply", issues=RATING_SCHEMA_SAID)
def rate(req: Request) -> Reply:
    score = _score(req.payload.get("risk_profile", {}))
    return Reply.acdc(
        recipient=req.sender,
        attributes={"score": score, "dt": req.now()},
        edges={"profile": {"cred_said": req.payload_said,
                           "schema_said": RATING_SCHEMA_SAID}} if req.payload_said else None,
    )
```

`service-aid/examples/rating_engine/schema/rating_result.json`:

```json
{
  "$id": "",
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "RatingResult",
  "type": "object",
  "properties": {
    "v": {"type": "string"},
    "d": {"type": "string"},
    "i": {"type": "string"},
    "ri": {"type": "string"},
    "s": {"type": "string"},
    "a": {
      "oneOf": [
        {"type": "string"},
        {
          "type": "object",
          "properties": {
            "d": {"type": "string"},
            "i": {"type": "string"},
            "dt": {"type": "string", "format": "date-time"},
            "score": {"type": "number"}
          },
          "additionalProperties": false,
          "required": ["d", "i", "dt", "score"]
        }
      ]
    }
  },
  "additionalProperties": false,
  "required": ["v", "d", "i", "ri", "s", "a"]
}
```

`service-aid/examples/rating_engine/app.py`:

```python
"""CDK app: shared core stack + the Rating Engine Service AID."""
import aws_cdk as cdk
from serviceaid.cdk import KeriCoreStack, ServiceAid

# The 5-witness federation (see project memory reference_witness_federation).
WITNESSES = [
    # "BWit1...", "BWit2...", "BWit3...", "BWit4...", "BWit5...",
]

app = cdk.App()
core = KeriCoreStack(app, "KeriCore", table_name="keri-core")

rating = cdk.Stack(app, "RatingEngine")
ServiceAid(
    rating, "Rating",
    alias="rating",
    core_table_name="keri-core",
    handler_module="rating_handler",   # bootstrap imports this module name
    witnesses=WITNESSES,
    toad=max(0, (len(WITNESSES) * 2 + 2) // 3) if WITNESSES else 0,
    image_directory=".",               # repo root as Docker context
)
rating.add_dependency(core)

app.synth()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest service-aid/tests/test_example_rating.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the entire framework suite once more**

Run: `.venv/bin/python -m pytest service-aid/tests/ tests/db/test_dynamodbing_namespace.py -v`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add service-aid/examples/ service-aid/tests/test_example_rating.py
git commit -m "feat(serviceaid): rating-engine reference example (handler + schema + CDK app)"
```

---

### Task 14: Local integration test against DynamoDB Local + README

**Why:** Spec §11 integration tier and a developer-facing README so the framework is usable. The handler e2e (Task 8) runs on moto; this task adds a DynamoDB-Local-backed smoke test mirroring `sam-witness/test_live.py`, gated so it skips when the endpoint isn't running, plus the docs.

**Files:**
- Create: `service-aid/tests/test_integration_local.py`, `service-aid/README.md`

- [ ] **Step 1: Write the integration test, skipped unless `SERVICEAID_ENDPOINT_URL` is set**

`service-aid/tests/test_integration_local.py`:

```python
import os
import base64

import pytest

ENDPOINT = os.environ.get("SERVICEAID_ENDPOINT_URL")
needs_local = pytest.mark.skipif(not ENDPOINT,
                                 reason="set SERVICEAID_ENDPOINT_URL to a DynamoDB Local URL")


@needs_local
def test_request_against_dynamodb_local(monkeypatch):
    """Full pipeline against a real DynamoDB (Local), not moto.

    Run DynamoDB Local first:
        docker run -p 8000:8000 amazon/dynamodb-local
        SERVICEAID_ENDPOINT_URL=http://localhost:8000 \
          .venv/bin/python -m pytest service-aid/tests/test_integration_local.py -v
    """
    from keri.app.habbing import Habery
    from keri.core.signing import Salter
    from keri.core import scheming
    from keri.kering import Kinds
    from keri.peer import exchanging
    from serviceaid import runtime, handler as H
    from serviceaid.config import Config
    from serviceaid.contract import service, Reply
    from _schema import RATING_SCHEMA_SAD

    # No Secrets Manager here: run without a bran (plaintext keeper) for the
    # local smoke test; encryption is exercised in the moto suite.
    cfg = Config(alias="rating", core_table="keri-core-local",
                 keeper_table="rating-ks-local", witnesses=[], toad=0,
                 handler_module="", bran_secret="",
                 region="us-east-1", endpoint_url=ENDPOINT)
    runtime.reset()
    service._commands.clear()
    service.schemas.clear()
    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)

    @service.command(route="/rate/apply", issues=schemer.said)
    def rate(req):
        return Reply.acdc(recipient=req.sender, attributes={"score": 700})

    state = runtime.init(cfg)
    state.hby.db.schema.pin(keys=(schemer.said,), val=schemer)

    caller_hby = Habery(name="caller", temp=True, salt=Salter(raw=b'caller9876543210').qb64)
    caller = caller_hby.makeHab(name="caller", transferable=True)
    exn, _ = exchanging.exchange(route="/rate/apply", attributes={"risk": 7}, sender=caller.pre)
    ims = bytearray(caller.makeOwnEvent(sn=0)); ims.extend(caller.endorse(exn, last=False))
    event = {"path": "/rate/apply", "httpMethod": "POST",
             "body": base64.b64encode(bytes(ims)).decode(), "isBase64Encoded": True}
    resp = H.handler(event, None)
    assert resp["statusCode"] == 200
    assert b"/ipex/grant" in resp["body"].encode("utf-8")
    caller_hby.close()
```

- [ ] **Step 2: Run it (skips without an endpoint) to verify the gate works**

Run: `.venv/bin/python -m pytest service-aid/tests/test_integration_local.py -v`
Expected: SKIPPED (1 skipped) — proves the gate. (Optionally run with DynamoDB Local per the docstring to see it PASS.)

- [ ] **Step 3: Write the README**

`service-aid/README.md`:

```markdown
# KERI Service AID Framework

Wrap any Python function as an autonomous KERI **Service AID**: it verifies a
signed `exn` caller (self-contained CESR), authorizes, runs your compute, and
replies with a signed **ACDC** delivered as an IPEX grant. Serverless on AWS
Lambda + DynamoDB. Generalizes `sam-witness`.

## Developer experience

```python
from serviceaid import service, Request, Reply

@service.command(route="/rate/apply", issues="ESchemaRatingResult...")
def rate(req: Request) -> Reply:
    score = run_my_model(req.payload["risk_profile"])
    return Reply.acdc(recipient=req.sender,
                      attributes={"score": score, "dt": req.now()},
                      edges={"profile": {"cred_said": req.payload_said,
                                         "schema_said": "ESchema..."}})
```

Deploy with a Python CDK app (see `examples/rating_engine/app.py`):
one shared `KeriCoreStack` per account + one `ServiceAid` per service.

## Architecture

- **Tier 1 (public KERI state):** pooled into the shared core DynamoDB table,
  namespaced per service (`{alias}:kel`, `{alias}:tel`).
- **Tier 2 (private keys):** an isolated, encrypted keeper table per service;
  the keeper passcode (`bran`) lives in Secrets Manager and engages keripy's
  at-rest encryption.
- **Tier 3 (your domain data):** your own store, owned by your stack.

## Testing

```bash
.venv/bin/python -m pytest service-aid/tests/ -v          # unit + moto integration
# Full pipeline against DynamoDB Local:
docker run -p 8000:8000 amazon/dynamodb-local
SERVICEAID_ENDPOINT_URL=http://localhost:8000 \
  .venv/bin/python -m pytest service-aid/tests/test_integration_local.py -v
```

## v1 scope & limits

Single transferable+witnessed AID; self-contained-CESR caller verification;
allowlist + required-credential authz; synchronous IPEX-grant ACDC reply;
idempotency. **Out (v2+):** watcher/cached key-state, async/long-running
compute, cross-runtime 1-of-N multisig, KMS-as-signer, non-Python compute.
High-rate KEL/TEL append serialization is v2 (see spec §14).
```

- [ ] **Step 4: Commit**

```bash
git add service-aid/tests/test_integration_local.py service-aid/README.md
git commit -m "test(serviceaid): DynamoDB-Local integration smoke test + README"
```

---

## Appendix A — Out-of-band hardening (spec §12.4, NOT v1 scope)

Tracked but deliberately **not** part of this plan's v1 deliverable. Decide with the user before scheduling.

**Existing `sam-witness` stores private keys in plaintext** in its `-ks` DynamoDB table and ships a plaintext `WITNESS_SALT` CloudFormation parameter (`witness_handler.py:45,101,115`; `template.yaml:24-27,116`). The same fix applies as Task 2/Task 7 here: pass a `bran` loaded from Secrets Manager to the witness's `Habery`, engaging aeid encryption, and drop the plaintext salt parameter. This is a separate change to `sam-witness/`, with its own deploy + key-migration considerations (existing plaintext keys must be re-encrypted or the witness re-incepted), so it is out of scope for the Service AID framework v1.

---

## Self-review notes (author)

- **Spec coverage:** §2 settled decisions → Tasks 2/4/7/8/12; §3 two-layer topology → Tasks 10/12; §4 layout → all (with flagged deviations); §5 contract → Task 3; §6 flow → Tasks 7/8; §7 keeper custody → Tasks 2/7; §8 namespacing → Task 1; §9 authz → Task 5; §10 errors/idempotency → Tasks 6/8; §11 testing → Tasks 8/14; §12 prereqs → Tasks 1/2/4 (+ Appendix A for §12.4); §13 v1 scope → all; §14 open questions → resolved inline (no-backer registry, env-import handler registration, pooled reger with separate namespace, KEL-append serialization deferred to v2).
- **Open risk:** the synchronous issuance (Task 4) and the caller-side self-contained-CESR assembly in the handler e2e (Task 8) are the two places most likely to need adjustment to this fork's exact APIs; both have explicit debugging pointers and are proven patterns from the sibling Locksmith repo.
- **Type consistency:** `Config.kel_namespace`/`tel_namespace`, `RuntimeState` fields, `Ledger.seen/record`, `Reply.kind` values (`acdc`/`none`/`reject`), and `Service.lookup`/`routes` are used identically across Tasks 3–13.
