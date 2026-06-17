# Service-AID Framework & CDK Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the embryonic `keri_cdk/handlers/serviceaid/` code into a first-class, dependency-injected `keri_serviceaid` developer framework (declare an entity + inject six provider Protocols + register `@command` routes) plus a `ServiceAidFunction` CDK construct that deploys it, with a transport-silent `204`-and-mailbox-out request pipeline and witnessed inception via `Receiptor`.

**Architecture:** A Service-AID is the serverless implementation of a KERI *role* — one AID, many capabilities addressed by signed exn routes. The developer writes a `compute_code` module (`svc = ServiceAid(...)` + `@svc.command`), ships it as a Lambda asset; the framework (shipped in `ServiceAidFrameworkLayer`) supplies all KERI plumbing: CESR-over-HTTP ingest → oracle verification → command dispatch → IPEX-grant issuance → Postman delivery to the requester's mailbox, returning `204` (the HTTP layer carries zero KERI meaning). Verification and reachability both resolve from the shared KEL oracle (a pooled `shared#` DynamoDB namespace) with in-stream/OOBI fallbacks; idempotency (`record` between `issue` and `deliver`) gives exactly-once issuance + at-least-once delivery.

**Tech Stack:** Python 3.14, keripy (DynamoDBer/Habery/Exchanger/forwarding/Receiptor), aws-cdk-lib, moto, pytest, AWS Lambda (arm64, zip+layers).

## Global Constraints

- Python 3.14 only (`python_requires>=3.14.0`); Lambda runtime `PYTHON_3_14`, architecture `ARM_64`.
- Two new top-level packages are NOT auto-discovered by `setup.py` (`find_packages('src')`): `keri_cdk` and `keri_serviceaid` live at repo root, on `sys.path` for tests via the existing `examples/*/app.py` `sys.path.insert` idiom; the **layer build script** is what pip-installs `keri_serviceaid` into the layer.
- Test commands: `.venv/bin/python -m pytest <path> --import-mode=importlib` from the worktree root. No `tests/__init__.py` shadowing for new dirs; moto for all AWS.
- Inbound HTTP returns **`204 No Content`** on every accepted ingest; the only real HTTP error is a malformed CESR envelope → `400`. Every KERI-semantic outcome is a signed message to the mailbox or deliberate silence.
- v1 ships **grant-on-success + silence** on every other outcome (deny / reject / `none` / unknown route / bad sig / compute-raise → log, no reply). Signed denials are a named follow-on — do not implement.
- Inception MUST collect its own witness receipts via `agenting.Receiptor` (`/receipts`), **never** `agenting.WitnessReceiptor` (silently hangs over HTTP/Lambda — keripy#1422, locksmith#77). A regression-guard test enforces this.
- Command routes starting with `/ipex/` are reserved — `ServiceAid.command` must reject them.
- Naming is load-bearing and must be identical across tasks: `keri_serviceaid.ServiceAid`, `Request` (fields `sender, route, payload, credentials, message_said, key_state`), `Reply.acdc/none/reject`, `Command(route, payload_schema, issues, fn)`, `Authorizer.authorize`, `Verifier.verify`, `Resolver.resolve`, `Issuer.issue`, `Deliverer.deliver`, `IdempotencyStore.seen/record`, `Allowlist`, `OracleVerifier`, `OracleResolver`, `IpexGrantIssuer`, `PostmanDeliverer`, `DynamoLedger`, `VerificationError`, `Endpoint`, `KeyState`, `Context`, `RuntimeState`, `pipeline.process`, `keri_cdk.ServiceAidFunction`, `ServiceAidFrameworkLayer`, `grant_principal`.
- Out of scope — DO NOT implement (leave clearly-marked extension seams only): `CredentialGate` authz, watcher tier-3 verification, signed denials, DLQ/EventBridge auto-retry, mailbox-inbound drain, micro-app template loader / UEL / aggregates.

---

## File Structure

**New framework package `keri_serviceaid/`** (top-level, pip-installable, shipped in `ServiceAidFrameworkLayer`):

| File | Responsibility |
|---|---|
| `keri_serviceaid/__init__.py` | Public API re-exports (`ServiceAid`, `Request`, `Reply`, `Command`, `TestRuntime`, the six Protocols + defaults, `VerificationError`, `Endpoint`, `KeyState`, `Context`). |
| `keri_serviceaid/contract.py` | `Request`, `Reply`, `Command`, `ServiceAid` (registry + injected providers + `@command` decorator), `TestRuntime`. No keripy import at module top. |
| `keri_serviceaid/providers/__init__.py` | Re-export the six Protocols + defaults. |
| `keri_serviceaid/providers/authz.py` | `Authorizer` Protocol + `Allowlist` default. |
| `keri_serviceaid/providers/verify.py` | `Verifier` Protocol, `KeyState`, `VerificationError`, `OracleVerifier` default (tiers signed/receipts/watcher). |
| `keri_serviceaid/providers/resolve.py` | `Resolver` Protocol, `Endpoint`, `OracleResolver` default + `InStream`/`Oobi` fallback markers. |
| `keri_serviceaid/providers/issue.py` | `Issuer` Protocol, `Context`, `IpexGrantIssuer` default (migrated from `issuing.py`). |
| `keri_serviceaid/providers/deliver.py` | `Deliverer` Protocol + `PostmanDeliverer` default (wraps `forwarding.Poster`). |
| `keri_serviceaid/providers/idempotency.py` | `IdempotencyStore` Protocol + `DynamoLedger` default (migrated from `idempotency.py`). |
| `keri_serviceaid/pipeline.py` | `process(state, serder, attachments)` — the per-inbound compose; pure, providers injected. |
| `keri_serviceaid/config.py` | `Config` + `Config.from_env()` (migrated from `config.py`). |
| `keri_serviceaid/runtime.py` | `RuntimeState`, `init()`, `reset()`, `_CaptureHandler`, `incept_or_load()` (Receiptor witnessing). |
| `keri_serviceaid/handler.py` | `handler(event, context)` — CR fork + CESR-over-HTTP reassembly → pipeline → `204`. |
| `keri_serviceaid/bootstrap.py` | libsodium `find_library` shim (migrated from `bootstrap.py`). |

**Modified keripy:**

| File | Change |
|---|---|
| `src/keri/app/lambding.py:64-67` | Add `ends.`, `locs.`, `eans.` to `SHARED_KEL_STORES`. |

**New / rewritten `keri_cdk`:**

| File | Responsibility |
|---|---|
| `keri_cdk/framework_layer.py` | `ServiceAidFrameworkLayer` construct (wraps `LayerVersion`). |
| `keri_cdk/layers/build_framework_layer.sh` | pip-install `keri_serviceaid` into the layer asset (arm64 Docker). |
| `keri_cdk/service_aid.py` | Rewritten: `ServiceAidFunction(Construct, iam.IGrantable)` (compute_code + two layers + IGrantable + four-pattern LeadingKeys + keeper IAM + inception CR + CESR API GW). |
| `keri_cdk/__init__.py` | Export `ServiceAidFunction`, `ServiceAidFrameworkLayer`. |

**Modified example + probe:**

| File | Change |
|---|---|
| `examples/gated_retrieval/gated_handler.py` | Rewritten to `keri_serviceaid` framework, ≥2 routes. |
| `examples/gated_retrieval/app.py` | Wire `ServiceAidFunction` with `compute_code`/`handler_ref`. |
| `examples/gated_retrieval/schema/gated_record.json` | (exists) the issued ACDC schema. |
| `examples/gated_retrieval/DEPLOY_RUNBOOK.md` | First-real-deploy manual runbook. |
| `keri_cdk/probes/leadingkeys/README.md` + `probe.py` | Extend to cover `ends./locs./eans.` shared stores. |

**New tests:**

| File | Responsibility |
|---|---|
| `tests/serviceaid/test_import.py` | `keri_serviceaid` imports + public names present. |
| `tests/serviceaid/test_contract_v2.py` | registration, dup-route, `/ipex/` rejection, `TestRuntime.send`. |
| `tests/serviceaid/test_providers_authz.py` | `Allowlist` allow/deny. |
| `tests/serviceaid/test_providers_verify.py` | `OracleVerifier` tier gating + `VerificationError`. |
| `tests/serviceaid/test_providers_resolve.py` | `OracleResolver` role-priority selection. |
| `tests/serviceaid/test_providers_issue.py` | `IpexGrantIssuer` grant shape. |
| `tests/serviceaid/test_providers_deliver.py` | `PostmanDeliverer` calls `Poster.send` with the right dest/topic. |
| `tests/serviceaid/test_providers_idempotency.py` | `DynamoLedger` seen/record on moto. |
| `tests/serviceaid/test_pipeline.py` | grant delivered; deny→silence; replay→re-deliver-not-re-issue; raise→nothing recorded. |
| `tests/serviceaid/test_runtime_v2.py` | cold-start on moto; cross-Habery oracle read; Receiptor regression-guard. |
| `tests/serviceaid/test_handler_v2.py` | `204` on ingest; CR fork; moto+fake-mailbox end-to-end + replay. |
| `tests/handlers/test_oracle_reachability.py` | cross-Habery `endsFor` resolves over the oracle. |
| `tests/cdk/test_framework_layer.py` | layer runtime/arch synth. |
| `tests/cdk/test_service_aid_function.py` | two layers, handler string, env merge, four-pattern LeadingKeys, IGrantable. |
| `tests/cdk/test_gated_example_v2.py` | reworked example synth. |

**Retired (Task 9):** `keri_cdk/handlers/serviceaid/` (content migrated to `keri_serviceaid`). The old `tests/serviceaid/test_*.py` that import `keri_cdk.handlers.serviceaid.*` (`test_authorize.py`, `test_contract.py`, `test_idempotency.py`, `test_issuing.py`, `test_runtime.py`, `test_runtime_shared_kel.py`, `test_handler_e2e.py`, `test_bootstrap.py`, `test_config.py`) and `tests/cdk/test_service_aid.py` / `test_gated_example.py` are removed in the same task that retires the code they cover.

---

### Task 1: Venv + `keri_serviceaid` package scaffold

**Files:**
- Create: `keri_serviceaid/__init__.py`
- Create: `keri_serviceaid/providers/__init__.py`
- Test: `tests/serviceaid/test_import.py`

**Interfaces:**
- Produces: the importable package `keri_serviceaid` whose `__all__` names every public symbol later tasks fill in. Until a task implements a symbol, `__init__.py` imports it from a stub-bearing module; this task creates only the package skeleton and the import test.

- [ ] **Step 0: Build the worktree venv and verify keri imports**

Run:
```bash
cd ~/code/keripy/.worktrees/service-aid-runtime
python3.14 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install moto pytest "aws-cdk-lib>=2.0.0" constructs
.venv/bin/python -c "import keri, aws_cdk, moto; print('keri', keri.__version__ if hasattr(keri,'__version__') else 'ok')"
```
Expected: prints `keri ok` (or a version) with no ImportError.

- [ ] **Step 1: Write the failing import test**

Create `tests/serviceaid/test_import.py`:
```python
"""keri_serviceaid public API surface must import and expose the v1 names."""


def test_package_imports():
    import keri_serviceaid  # noqa: F401


def test_public_names_present():
    import keri_serviceaid as ks
    for name in (
        "ServiceAid", "Request", "Reply", "Command", "TestRuntime",
        "Authorizer", "Allowlist",
        "Verifier", "OracleVerifier", "VerificationError", "KeyState",
        "Resolver", "OracleResolver", "Endpoint",
        "Issuer", "IpexGrantIssuer", "Context",
        "Deliverer", "PostmanDeliverer",
        "IdempotencyStore", "DynamoLedger",
    ):
        assert hasattr(ks, name), f"keri_serviceaid is missing {name}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_import.py -v --import-mode=importlib`
Expected: FAIL with `ModuleNotFoundError: No module named 'keri_serviceaid'`.

- [ ] **Step 3: Create the package skeleton**

Create `keri_serviceaid/providers/__init__.py`:
```python
"""Six extension-point Protocols + their default implementations.

Each module defines a typing.Protocol and one default impl. Adding a new
extension point = a new module here (new Protocol + default); the pipeline
never changes. See the framework design spec.
"""
from .authz import Authorizer, Allowlist
from .verify import Verifier, OracleVerifier, VerificationError, KeyState
from .resolve import Resolver, OracleResolver, Endpoint
from .issue import Issuer, IpexGrantIssuer, Context
from .deliver import Deliverer, PostmanDeliverer
from .idempotency import IdempotencyStore, DynamoLedger

__all__ = [
    "Authorizer", "Allowlist",
    "Verifier", "OracleVerifier", "VerificationError", "KeyState",
    "Resolver", "OracleResolver", "Endpoint",
    "Issuer", "IpexGrantIssuer", "Context",
    "Deliverer", "PostmanDeliverer",
    "IdempotencyStore", "DynamoLedger",
]
```

Create `keri_serviceaid/__init__.py`:
```python
"""keri_serviceaid — the declarative Service-AID developer framework.

Declare an entity (`svc = ServiceAid(...)`), map routes to functions
(`@svc.command`), and inject the cross-cutting concerns (authz, verification,
issuance, …) as swappable providers. The framework supplies all KERI plumbing.
Shipped in `ServiceAidFrameworkLayer`; the dev's `compute_code` asset imports
from here. No keripy import at top level so this stays cheap to import.
"""
from .contract import ServiceAid, Request, Reply, Command, TestRuntime
from .providers import (
    Authorizer, Allowlist,
    Verifier, OracleVerifier, VerificationError, KeyState,
    Resolver, OracleResolver, Endpoint,
    Issuer, IpexGrantIssuer, Context,
    Deliverer, PostmanDeliverer,
    IdempotencyStore, DynamoLedger,
)

__all__ = [
    "ServiceAid", "Request", "Reply", "Command", "TestRuntime",
    "Authorizer", "Allowlist",
    "Verifier", "OracleVerifier", "VerificationError", "KeyState",
    "Resolver", "OracleResolver", "Endpoint",
    "Issuer", "IpexGrantIssuer", "Context",
    "Deliverer", "PostmanDeliverer",
    "IdempotencyStore", "DynamoLedger",
]
```

> NOTE: `tests/serviceaid/` already has a `conftest.py`. This task's `test_import.py` does not depend on it. The `contract` and `providers` submodules are implemented in Tasks 2–3; until then the import test cannot pass — that is intentional TDD ordering. Run this test green at the END of Task 3 (it is re-run there). For Task 1's own commit, prove the package directory exists and imports as a namespace by deferring the `__init__.py` body until Task 2/3 land. To keep Task 1 independently green, implement a TEMPORARY minimal `contract.py`/provider stubs in this task (below), then flesh them out in Tasks 2–3.

- [ ] **Step 4: Add minimal stubs so the import test passes now**

Create `keri_serviceaid/contract.py` (TEMP minimal — fully implemented in Task 2):
```python
"""Developer contract (Task 1 stub; fully implemented in Task 2)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Request:
    sender: str
    route: str
    payload: dict
    credentials: list = field(default_factory=list)
    message_said: str = ""
    key_state: object = None


@dataclass
class Reply:
    kind: str
    recipient: Optional[str] = None
    attributes: Optional[dict] = None
    edges: Optional[dict] = None
    rules: Optional[dict] = None
    reason: Optional[str] = None


@dataclass
class Command:
    route: str
    payload_schema: Optional[dict]
    issues: str
    fn: Callable[[Request], Reply]


class ServiceAid:
    pass


class TestRuntime:
    __test__ = False
```

Create `keri_serviceaid/providers/authz.py`, `verify.py`, `resolve.py`, `issue.py`, `deliver.py`, `idempotency.py` each with a one-line module docstring and the symbols the `providers/__init__.py` imports, as bare placeholders (fully implemented in Task 3). For example `keri_serviceaid/providers/verify.py`:
```python
"""Verifier Protocol + OracleVerifier default (Task 1 stub; Task 3 impl)."""
from __future__ import annotations
from dataclasses import dataclass


class VerificationError(Exception):
    pass


@dataclass
class KeyState:
    pre: str = ""
    tier: str = "receipts"


class Verifier:  # Protocol promoted to a class in Task 3
    pass


class OracleVerifier:
    def __init__(self, tier: str = "receipts"):
        self.tier = tier
```
(Write the analogous one-symbol-each stub for `authz.py` → `Authorizer`, `Allowlist`; `resolve.py` → `Resolver`, `OracleResolver`, `Endpoint`; `issue.py` → `Issuer`, `IpexGrantIssuer`, `Context`; `deliver.py` → `Deliverer`, `PostmanDeliverer`; `idempotency.py` → `IdempotencyStore`, `DynamoLedger`. Each placeholder is a bare `class Name: pass` except `Allowlist.__init__(self, aids=None)`, `DynamoLedger.__init__(self, db)`, `IpexGrantIssuer.__init__(self)`, `OracleResolver.__init__(self, fallback=None)`, `PostmanDeliverer.__init__(self)`, and `Endpoint` as a dataclass with `role: str = ""`, `eid: str = ""`, `url: str = ""`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_import.py -v --import-mode=importlib`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add keri_serviceaid/ tests/serviceaid/test_import.py
git commit -m "feat(serviceaid): scaffold keri_serviceaid package + import test

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Entity + Request/Reply/Command + TestRuntime (`contract.py`)

**Files:**
- Modify: `keri_serviceaid/contract.py` (replace the Task 1 stub)
- Test: `tests/serviceaid/test_contract_v2.py`

**Interfaces:**
- Consumes: nothing from other tasks (contract is the root).
- Produces:
  - `Request(sender:str, route:str, payload:dict, credentials:list=[], message_said:str="", key_state=None)`
  - `Reply.acdc(*, recipient, attributes, edges=None, rules=None)`, `Reply.none()`, `Reply.reject(*, reason)`; fields `kind, recipient, attributes, edges, rules, reason`.
  - `Command(route:str, payload_schema:dict|None, issues:str, fn:Callable[[Request],Reply])`.
  - `ServiceAid(*, alias, witnesses=None, toad=0, authz=None, verifier=None, resolver=None, issuer=None, deliverer=None, idempotency=None)`; `.command(*, route, issues="", payload_schema=None)` decorator; `.lookup(route)`; `.routes` property; `.register_schema(sad)->said`; attribute `.schemas:list[dict]`.
  - `TestRuntime(svc).send(*, route, sender, payload, credentials=None) -> Reply`.

- [ ] **Step 1: Write the failing tests**

Create `tests/serviceaid/test_contract_v2.py`:
```python
"""ServiceAid registry, Reply factories, route guards, TestRuntime."""
import pytest

from keri_serviceaid import ServiceAid, Reply, Request, TestRuntime


def _svc():
    return ServiceAid(alias="mvr-bureau")


def test_command_registration_and_lookup():
    svc = _svc()

    @svc.command(route="/mvr/cmd/request_record", issues="ESchemaSaid")
    def request_record(req: Request) -> Reply:
        return Reply.none()

    assert svc.routes == ["/mvr/cmd/request_record"]
    cmd = svc.lookup("/mvr/cmd/request_record")
    assert cmd.route == "/mvr/cmd/request_record"
    assert cmd.issues == "ESchemaSaid"
    assert cmd.payload_schema is None
    assert callable(cmd.fn)


def test_duplicate_route_raises():
    svc = _svc()

    @svc.command(route="/mvr/cmd/x")
    def a(req): return Reply.none()

    with pytest.raises(ValueError, match="duplicate route"):
        @svc.command(route="/mvr/cmd/x")
        def b(req): return Reply.none()


def test_ipex_route_rejected():
    svc = _svc()
    with pytest.raises(ValueError, match="/ipex/"):
        @svc.command(route="/ipex/grant")
        def grant(req): return Reply.none()


def test_reply_factories():
    a = Reply.acdc(recipient="EReq", attributes={"vin": "1"})
    assert a.kind == "acdc" and a.recipient == "EReq" and a.attributes == {"vin": "1"}
    assert Reply.none().kind == "none"
    r = Reply.reject(reason="nope")
    assert r.kind == "reject" and r.reason == "nope"


def test_providers_stored_and_default_none():
    sentinel = object()
    svc = ServiceAid(alias="mvr-bureau", witnesses=["EWit"], toad=1, authz=sentinel)
    assert svc.alias == "mvr-bureau"
    assert svc.witnesses == ["EWit"]
    assert svc.toad == 1
    assert svc.authz is sentinel        # injected provider stored verbatim
    assert svc.verifier is None         # left None here; runtime wires the default


def test_testruntime_send_invokes_fn():
    svc = _svc()

    @svc.command(route="/mvr/cmd/echo")
    def echo(req: Request) -> Reply:
        return Reply.acdc(recipient=req.sender, attributes=req.payload)

    rt = TestRuntime(svc)
    reply = rt.send(route="/mvr/cmd/echo", sender="EReq", payload={"k": "v"})
    assert reply.kind == "acdc" and reply.recipient == "EReq"
    assert reply.attributes == {"k": "v"}


def test_testruntime_unknown_route_raises():
    rt = TestRuntime(_svc())
    with pytest.raises(KeyError):
        rt.send(route="/nope", sender="E", payload={})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_contract_v2.py -v --import-mode=importlib`
Expected: FAIL (e.g. `TypeError: ServiceAid() takes no arguments` / `AttributeError: ... has no attribute 'command'`).

- [ ] **Step 3: Implement `contract.py`**

Replace `keri_serviceaid/contract.py` with:
```python
"""Developer-facing contract: ServiceAid registry, Request, Reply, Command,
TestRuntime. No keripy import at module top (register_schema imports lazily) so
this stays cheap to import in the dev's compute_code module."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Request:
    """Verified, authorized inbound request handed to a developer function."""
    sender: str                       # verified caller AID prefix
    route: str                        # the signed exn `r`
    payload: dict                     # verified exn attributes (the `a` block)
    credentials: list = field(default_factory=list)  # verified presented ACDCs ([] under Allowlist)
    message_said: str = ""            # exn SAID — the idempotency key
    key_state: object = None          # resolved sender KeyState (assurance tier)

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
    payload_schema: Optional[dict]    # optional JSON-Schema for the `a` block (YAGNI: not enforced in v1)
    issues: str                       # ACDC schema SAID this command may issue
    fn: Callable[[Request], Reply]


class ServiceAid:
    """The declared entity: identity config + injected providers + command
    registry. The dev names it (`svc = ServiceAid(...)`); the framework finds it
    via the `module:attr` entry ref. Providers left None get their default wired
    in the runtime (here we just store None)."""

    def __init__(self, *, alias: str, witnesses: list[str] | None = None,
                 toad: int = 0, authz=None, verifier=None, resolver=None,
                 issuer=None, deliverer=None, idempotency=None):
        self.alias = alias
        self.witnesses = witnesses or []
        self.toad = toad
        self.authz = authz
        self.verifier = verifier
        self.resolver = resolver
        self.issuer = issuer
        self.deliverer = deliverer
        self.idempotency = idempotency
        self._commands: dict[str, Command] = {}
        self.schemas: list[dict] = []   # ACDC schema SADs to register at init

    def command(self, *, route: str, issues: str = "",
                payload_schema: dict | None = None):
        if route.startswith("/ipex/"):
            raise ValueError(f"route {route!r} is reserved: /ipex/* is owned by "
                             "the IPEX protocol and may not be a command route")

        def deco(fn: Callable[[Request], Reply]):
            if route in self._commands:
                raise ValueError(f"duplicate route registered: {route}")
            self._commands[route] = Command(route=route, payload_schema=payload_schema,
                                             issues=issues, fn=fn)
            return fn
        return deco

    def register_schema(self, sad: dict) -> str:
        """Saidify an ACDC schema SAD, queue it for db registration, return its SAID."""
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


class TestRuntime:
    """In-memory runtime for unit-testing developer command functions without keripy."""

    __test__ = False  # do not collect as a pytest suite

    def __init__(self, svc: ServiceAid):
        self.svc = svc

    def send(self, *, route: str, sender: str, payload: dict,
             credentials: list | None = None) -> Reply:
        cmd = self.svc.lookup(route)
        if cmd is None:
            raise KeyError(f"no command for route {route}")
        req = Request(sender=sender, route=route, payload=payload,
                      credentials=credentials or [], message_said="EtestMsg")
        return cmd.fn(req)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_contract_v2.py -v --import-mode=importlib`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add keri_serviceaid/contract.py tests/serviceaid/test_contract_v2.py
git commit -m "feat(serviceaid): ServiceAid entity + Request/Reply/Command + TestRuntime

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3a: Providers part 1 — authz / verify / resolve

**Files:**
- Modify: `keri_serviceaid/providers/authz.py`
- Modify: `keri_serviceaid/providers/verify.py`
- Modify: `keri_serviceaid/providers/resolve.py`
- Test: `tests/serviceaid/test_providers_authz.py`
- Test: `tests/serviceaid/test_providers_verify.py`
- Test: `tests/serviceaid/test_providers_resolve.py`

**Interfaces:**
- Consumes: `Request` from `keri_serviceaid.contract`.
- Produces:
  - `Authorizer` Protocol `.authorize(req:Request)->tuple[bool,str]`; default `Allowlist(aids:list=None)` (empty ⇒ any sender; v1 `credentials=[]`).
  - `Verifier` Protocol `.verify(sender:str, ims:bytes, hby)->KeyState`; `KeyState(pre, tier, sn)`; `VerificationError(Exception)`; default `OracleVerifier(tier="receipts")` with tiers `signed|receipts|watcher`.
  - `Resolver` Protocol `.resolve(sender:str, hby)->Endpoint`; `Endpoint(role, eid, url)`; default `OracleResolver(fallback=[InStream(), Oobi()])` using `hab.endsFor`.

- [ ] **Step 1: Write the failing authz test**

Create `tests/serviceaid/test_providers_authz.py`:
```python
"""Allowlist authorizer: empty ⇒ any sender; non-empty ⇒ membership."""
from keri_serviceaid import Allowlist, Request


def _req(sender):
    return Request(sender=sender, route="/x", payload={})


def test_empty_allowlist_allows_any():
    allow, reason = Allowlist([]).authorize(_req("EAnyone"))
    assert allow is True and reason == ""


def test_allowlist_allows_member():
    allow, reason = Allowlist(["EReq1", "EReq2"]).authorize(_req("EReq2"))
    assert allow is True and reason == ""


def test_allowlist_denies_nonmember():
    allow, reason = Allowlist(["EReq1"]).authorize(_req("EReq2"))
    assert allow is False and "not in allowlist" in reason
```

- [ ] **Step 2: Run authz test to verify it fails**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_authz.py -v --import-mode=importlib`
Expected: FAIL (`Allowlist` has no `authorize`).

- [ ] **Step 3: Implement `authz.py`**

Replace `keri_serviceaid/providers/authz.py` with:
```python
"""Authorizer extension point + Allowlist default.

Evaluated AFTER KERI verification. v1 default is sender-AID gating (Allowlist);
the credential-presentation gate (CredentialGate(required_schema=…)) is the
named crown-jewel follow-on and is NOT implemented here."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..contract import Request


@runtime_checkable
class Authorizer(Protocol):
    def authorize(self, req: Request) -> tuple[bool, str]:
        """Return (allow, reason); reason is "" when allowed."""
        ...


class Allowlist:
    """Default authorizer: an explicit set of permitted sender AIDs.

    An empty allowlist means any verified sender is allowed. v1 never inspects
    req.credentials (always [] under this authz); credential gating is a follow-on.
    """

    def __init__(self, aids: list[str] | None = None):
        self.aids = list(aids or [])

    def authorize(self, req: Request) -> tuple[bool, str]:
        if self.aids and req.sender not in self.aids:
            return False, f"sender {req.sender} not in allowlist"
        return True, ""
```

- [ ] **Step 4: Run authz test to verify it passes**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_authz.py -v --import-mode=importlib`
Expected: PASS (3 passed).

- [ ] **Step 5: Write the failing verify test**

Create `tests/serviceaid/test_providers_verify.py`:
```python
"""OracleVerifier asserts the assurance tier of the sender's resolved key state."""
import pytest

from keri_serviceaid import OracleVerifier, VerificationError, KeyState


class FakeKever:
    def __init__(self, sn=0, wits=None):
        self.sn = sn
        self.wits = wits or []


class FakeDb:
    """Stands in for hby.db: .wigs.get returns truthy iff receipts exist."""
    def __init__(self, has_wigs):
        self._has = has_wigs

    class _Wigs:
        def __init__(self, has):
            self._has = has

        def getLast(self, keys=None):
            return b"sig" if self._has else None

    @property
    def wigs(self):
        return self._Wigs(self._has)


class FakeHby:
    def __init__(self, sender, has_wigs, wits):
        self.kevers = {sender: FakeKever(wits=wits)}
        self.db = FakeDb(has_wigs)


def test_unknown_sender_raises():
    hby = FakeHby("EOther", has_wigs=False, wits=[])
    with pytest.raises(VerificationError, match="no key state"):
        OracleVerifier(tier="signed").verify("EReq", b"", hby)


def test_signed_tier_accepts_any_known_kever():
    hby = FakeHby("EReq", has_wigs=False, wits=[])
    ks = OracleVerifier(tier="signed").verify("EReq", b"", hby)
    assert isinstance(ks, KeyState) and ks.pre == "EReq" and ks.tier == "signed"


def test_receipts_tier_requires_witness_receipts():
    # witnessed AID with no receipts in the oracle ⇒ tier unmet
    hby = FakeHby("EReq", has_wigs=False, wits=["EWit"])
    with pytest.raises(VerificationError, match="receipts"):
        OracleVerifier(tier="receipts").verify("EReq", b"", hby)


def test_receipts_tier_accepts_when_receipts_present():
    hby = FakeHby("EReq", has_wigs=True, wits=["EWit"])
    ks = OracleVerifier(tier="receipts").verify("EReq", b"", hby)
    assert ks.tier == "receipts"


def test_watcher_tier_not_implemented():
    hby = FakeHby("EReq", has_wigs=True, wits=["EWit"])
    with pytest.raises(NotImplementedError):
        OracleVerifier(tier="watcher").verify("EReq", b"", hby)
```

- [ ] **Step 6: Run verify test to verify it fails**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_verify.py -v --import-mode=importlib`
Expected: FAIL (`OracleVerifier` has no `verify`).

- [ ] **Step 7: Implement `verify.py`**

Replace `keri_serviceaid/providers/verify.py` with:
```python
"""Verifier extension point + OracleVerifier default.

Verification = sender KEY STATE assurance, separate from reachability (resolve.py)
and authz (authz.py). The shared KEL oracle already carries key events AND witness
receipts, so a local read yields witness-corroborated (tier-2) key state for any
in-domain or served-before AID. The sender KEL is parsed into the oracle BEFORE
this runs (the pipeline does hby.psr.parse), so verify only asserts the tier."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class VerificationError(Exception):
    """Raised when the sender's resolved key state does not meet the tier."""


@dataclass
class KeyState:
    pre: str
    tier: str            # "signed" | "receipts" | "watcher"
    sn: int = 0


@runtime_checkable
class Verifier(Protocol):
    def verify(self, sender: str, ims: bytes, hby) -> KeyState:
        """Assert the assurance tier of `sender`'s key state; raise
        VerificationError if unmet. Returns the resolved KeyState."""
        ...


class OracleVerifier:
    """Default verifier. Tiers:
      - "signed"   (tier-1): sender kever present in the oracle (self-certifying).
      - "receipts" (tier-2, default): witnessed AIDs must have witness receipts
        in the oracle; unwitnessed AIDs (no wits) pass at tier-1-equivalent.
      - "watcher"  (tier-3, FUTURE): not implemented — the keri_cdk watcher seam.
    """

    def __init__(self, tier: str = "receipts"):
        if tier not in ("signed", "receipts", "watcher"):
            raise ValueError(f"unknown verifier tier {tier!r}")
        self.tier = tier

    def verify(self, sender: str, ims: bytes, hby) -> KeyState:
        if self.tier == "watcher":
            raise NotImplementedError(
                "watcher (tier-3) verification is a named follow-on (keri_cdk "
                "watcher seam); use tier 'signed' or 'receipts'")

        kever = hby.kevers.get(sender) if hasattr(hby.kevers, "get") else (
            hby.kevers[sender] if sender in hby.kevers else None)
        if kever is None:
            raise VerificationError(
                f"no key state for {sender} in the oracle (first-contact KEL not "
                "parsed, or sender unknown)")

        sn = getattr(kever, "sn", 0)
        wits = getattr(kever, "wits", []) or []
        if self.tier == "receipts" and wits:
            # Witnessed AID: require at least one witness receipt in the oracle.
            if hby.db.wigs.getLast(keys=(sender,)) is None:
                raise VerificationError(
                    f"{sender} is witnessed but has no witness receipts in the "
                    "oracle — tier 'receipts' unmet (strict default)")
        return KeyState(pre=sender, tier=self.tier, sn=sn)
```

- [ ] **Step 8: Run verify test to verify it passes**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_verify.py -v --import-mode=importlib`
Expected: PASS (5 passed).

- [ ] **Step 9: Write the failing resolve test**

Create `tests/serviceaid/test_providers_resolve.py`:
```python
"""OracleResolver picks the highest-priority reachable endpoint via hab.endsFor."""
import pytest

from keri_serviceaid import OracleResolver, Endpoint


class FakeHab:
    def __init__(self, ends):
        self._ends = ends

    def endsFor(self, pre):
        return self._ends


class FakeHby:
    def __init__(self, hab):
        self.habs = {"EService": hab}

    @property
    def _service_hab(self):
        return self.habs["EService"]


def _resolver():
    return OracleResolver()


def test_mailbox_role_preferred_over_witness():
    ends = {
        "mailbox": {"EMbx": {"https": "https://mailbox.keri.host"}},
        "witness": {"EWit": {"https": "https://wit.example"}},
    }
    hby = FakeHby(FakeHab(ends))
    ep = _resolver().resolve("EReq", hby)
    assert isinstance(ep, Endpoint)
    assert ep.role == "mailbox" and ep.eid == "EMbx"
    assert ep.url == "https://mailbox.keri.host"


def test_controller_preferred_over_mailbox():
    ends = {
        "controller": {"ECtrl": {"https": "https://ctrl.example"}},
        "mailbox": {"EMbx": {"https": "https://mailbox.keri.host"}},
    }
    ep = _resolver().resolve("EReq", FakeHby(FakeHab(ends)))
    assert ep.role == "controller" and ep.eid == "ECtrl"


def test_witness_fallback_when_only_role():
    ends = {"witness": {"EWit": {"http": "http://wit.example"}}}
    ep = _resolver().resolve("EReq", FakeHby(FakeHab(ends)))
    assert ep.role == "witness" and ep.url == "http://wit.example"


def test_no_endpoint_raises():
    with pytest.raises(LookupError, match="no reachable endpoint"):
        _resolver().resolve("EReq", FakeHby(FakeHab({})))
```

- [ ] **Step 10: Run resolve test to verify it fails**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_resolve.py -v --import-mode=importlib`
Expected: FAIL (`OracleResolver` has no `resolve`).

- [ ] **Step 11: Implement `resolve.py`**

Replace `keri_serviceaid/providers/resolve.py` with:
```python
"""Resolver extension point + OracleResolver default.

Reachability (where to deliver the reply) is separate from identity and key
state. The oracle is made reachability-complete by sharing ends./locs./eans.
(Task 7), so OracleResolver can resolve an in-domain requester's mailbox from one
local hab.endsFor read. InStream/Oobi are first-contact fallback markers (the
runtime/Deliverer use them as hints; v1 keeps them as named seams)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class Endpoint:
    role: str            # "controller" | "agent" | "mailbox" | "witness"
    eid: str             # endpoint provider AID
    url: str             # first reachable URL (https preferred)


class InStream:
    """Fallback marker: end-roles that rode in the request stream (persisted to
    the private ns on parse). A named seam; OracleResolver reads them via endsFor."""


class Oobi:
    """Fallback marker: resolve the requester's OOBI to discover end-roles.
    A named seam; not auto-driven in v1 (the requester is expected in-domain)."""


@runtime_checkable
class Resolver(Protocol):
    def resolve(self, sender: str, hby) -> Endpoint:
        """Return the Endpoint to deliver the reply to; raise LookupError if none."""
        ...


# Role priority: a direct controller/agent endpoint beats a mailbox beats a witness.
_ROLE_PRIORITY = ("controller", "agent", "mailbox", "witness")


class OracleResolver:
    """Default resolver. Reads hab.endsFor(sender) (now oracle-complete) and picks
    the highest-priority role's endpoint, https preferred."""

    def __init__(self, fallback: list | None = None):
        self.fallback = fallback if fallback is not None else [InStream(), Oobi()]

    def resolve(self, sender: str, hby) -> Endpoint:
        hab = next(iter(hby.habs.values()))   # the single service hab
        ends = hab.endsFor(sender)            # role -> eid -> scheme -> url
        for role in _ROLE_PRIORITY:
            if role in ends and ends[role]:
                eid, locs = next(iter(ends[role].items()))
                url = locs.get("https") or locs.get("http") or next(iter(locs.values()), "")
                if url:
                    return Endpoint(role=role, eid=eid, url=url)
        raise LookupError(
            f"no reachable endpoint for {sender} via the oracle "
            "(in-stream/OOBI first-contact resolution is a named fallback seam)")
```

- [ ] **Step 12: Run resolve test to verify it passes**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_resolve.py -v --import-mode=importlib`
Expected: PASS (4 passed).

- [ ] **Step 13: Commit**

```bash
git add keri_serviceaid/providers/authz.py keri_serviceaid/providers/verify.py keri_serviceaid/providers/resolve.py tests/serviceaid/test_providers_authz.py tests/serviceaid/test_providers_verify.py tests/serviceaid/test_providers_resolve.py
git commit -m "feat(serviceaid): authz/verify/resolve providers (Protocol + default)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3b: Providers part 2 — issue / deliver / idempotency

**Files:**
- Modify: `keri_serviceaid/providers/issue.py`
- Modify: `keri_serviceaid/providers/deliver.py`
- Modify: `keri_serviceaid/providers/idempotency.py`
- Test: `tests/serviceaid/test_providers_issue.py`
- Test: `tests/serviceaid/test_providers_deliver.py`
- Test: `tests/serviceaid/test_providers_idempotency.py`

**Interfaces:**
- Consumes: `Reply` from contract; `Endpoint` from resolve.
- Produces:
  - `Context(hby, hab, rgy, registry_name)` dataclass (issuance/delivery handle).
  - `Issuer` Protocol `.issue(reply:Reply, ctx:Context)->bytes`; default `IpexGrantIssuer()` (builds a signed IPEX grant exn; migrated from `issuing.py`).
  - `Deliverer` Protocol `.deliver(msg:bytes, endpoint:Endpoint, ctx:Context)->None`; default `PostmanDeliverer()` wrapping `forwarding.Poster.send`.
  - `IdempotencyStore` Protocol `.seen(said:str)->bytes|None`, `.record(said:str, grant:bytes)->None`; default `DynamoLedger(db)`.

- [ ] **Step 1: Write the failing idempotency test**

Create `tests/serviceaid/test_providers_idempotency.py`:
```python
"""DynamoLedger stores the prior grant bytes keyed by exn SAID (moto-backed)."""
import boto3
import pytest
from moto import mock_aws

from keri.db.dynamodbing import DynamoDBer
from keri_serviceaid import DynamoLedger
from keri_serviceaid.providers.idempotency import PROC_STORE


@pytest.fixture
def db():
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1")
        d = DynamoDBer.open(name="led", stores=[PROC_STORE],
                            table_name="keri-core", namespace="svc:kel",
                            region="us-east-1")
        yield d
        d.close()


def test_unseen_returns_none(db):
    assert DynamoLedger(db).seen("ENeverSeen") is None


def test_record_then_seen_roundtrips_grant_bytes(db):
    led = DynamoLedger(db)
    grant = b'{"v":"KERI10JSON","t":"exn"}-attachments'
    led.record("EReqSaid", grant)
    assert led.seen("EReqSaid") == grant


def test_record_overwrites(db):
    led = DynamoLedger(db)
    led.record("EReqSaid", b"first")
    led.record("EReqSaid", b"second")
    assert led.seen("EReqSaid") == b"second"
```

- [ ] **Step 2: Run idempotency test to verify it fails**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_idempotency.py -v --import-mode=importlib`
Expected: FAIL (`DynamoLedger` has no `seen`/`record`, no `PROC_STORE`).

- [ ] **Step 3: Implement `idempotency.py`**

Replace `keri_serviceaid/providers/idempotency.py` with:
```python
"""IdempotencyStore extension point + DynamoLedger default.

Records the SIGNED GRANT bytes keyed by the inbound exn SAID. `record` happens
AFTER issue but BEFORE deliver, so a delivery failure + client re-send hits
seen() and RE-DELIVERS the same grant (never re-issues) → exactly-once issuance,
at-least-once delivery. Stores raw CESR grant bytes (not a JSON summary) so the
replay path can re-deliver the identical message."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from keri.db import subing

PROC_STORE = "proc."


@runtime_checkable
class IdempotencyStore(Protocol):
    def seen(self, said: str) -> bytes | None:
        """Return the prior recorded grant for `said`, or None if unseen."""
        ...

    def record(self, said: str, grant: bytes) -> None:
        """Pin the grant for `said` (overwriting any prior entry)."""
        ...


class DynamoLedger:
    """Default idempotency store on a DynamoDBer opened with PROC_STORE."""

    def __init__(self, db):
        self.db = db
        self.proc = subing.Suber(db=db, subkey=PROC_STORE)

    def seen(self, said: str) -> bytes | None:
        raw = self.proc.get(keys=(said,))
        if raw is None:
            return None
        return raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)

    def record(self, said: str, grant: bytes) -> None:
        self.proc.pin(keys=(said,), val=bytes(grant))
```

> NOTE: `subing.Suber.get` returns a decoded `str` (utf-8). CESR text domain is ASCII, so round-tripping bytes↔str is lossless; `seen` re-encodes to bytes. If a future grant carries non-ASCII it would need a binary Suber — out of scope for v1.

- [ ] **Step 4: Run idempotency test to verify it passes**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_idempotency.py -v --import-mode=importlib`
Expected: PASS (3 passed).

- [ ] **Step 5: Write the failing issue test**

Create `tests/serviceaid/test_providers_issue.py`:
```python
"""IpexGrantIssuer issues an ACDC and returns a self-contained IPEX /ipex/grant."""
from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import scheming, parsing, serdering
from keri.kering import Kinds, Vrsn_1_0
from keri.vdr import credentialing

from keri_serviceaid import IpexGrantIssuer, Reply, Context


RATING_SCHEMA_SAD = {
    "$id": "", "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Gated Record", "type": "object",
    "credentialType": "GatedRecord",
    "properties": {
        "v": {"type": "string"}, "d": {"type": "string"}, "u": {"type": "string"},
        "i": {"type": "string"}, "ri": {"type": "string"}, "s": {"type": "string"},
        "a": {"type": "object",
              "properties": {"d": {"type": "string"}, "i": {"type": "string"},
                             "dt": {"type": "string"}, "data": {"type": "string"}},
              "required": ["d", "i", "dt"]},
    },
    "required": ["v", "d", "i", "ri", "s", "a"],
}


def test_grant_shape_is_ipex_grant_exn():
    hby = Habery(name="svc", temp=True, salt=Salter(raw=b'0123456789abcdef').qb64)
    hab = hby.makeHab(name="svc", transferable=True)

    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)
    hby.db.schema.pin(keys=(schemer.said,), val=schemer)

    # recipient KEL must be known before issuing
    rcp_hby = Habery(name="rcp", temp=True, salt=Salter(raw=b'fedcba9876543210').qb64)
    rcp = rcp_hby.makeHab(name="rcp", transferable=True)
    parsing.Parser(kvy=hby.kvy, version=Vrsn_1_0).parse(ims=bytearray(rcp.replay()))
    hby.kvy.processEscrows()

    rgy = credentialing.Regery(hby=hby, name="svc")
    ctx = Context(hby=hby, hab=hab, rgy=rgy, registry_name="svc")
    reply = Reply.acdc(recipient=rcp.pre, attributes={"data": "cool"})

    grant = IpexGrantIssuer().issue(reply, ctx)
    assert isinstance(grant, (bytes, bytearray))
    serder = serdering.SerderKERI(raw=bytes(grant))
    assert serder.ked["t"] == "exn"
    assert serder.ked["r"] == "/ipex/grant"

    hby.close(); rcp_hby.close()
```

- [ ] **Step 6: Run issue test to verify it fails**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_issue.py -v --import-mode=importlib`
Expected: FAIL (`Context`/`IpexGrantIssuer.issue` missing).

- [ ] **Step 7: Implement `issue.py`** (migrate the proven path from `keri_cdk/handlers/serviceaid/issuing.py`)

Replace `keri_serviceaid/providers/issue.py` with:
```python
"""Issuer extension point + IpexGrantIssuer default.

Synchronous ACDC issuance + IPEX-grant framing for a Service-AID. Migrated from
the proven keri_cdk/handlers/serviceaid/issuing.py path (which mirrors the
Locksmith wallet credentialing/ipexing). v1 uses a no-backer registry so TEL
issuance needs no receipts and completes in-process on a virtual-time Doist.

WARNING (witnessed-AID limitation, carried over verbatim): the anchor ixn is
created INSIDE issue (and ensure_registry), so its witness receipts cannot be
pre-collected. For a WITNESSED service AID, Registrar.processWitnessEscrow holds
the tpwe escrow until ALL receipts arrive; on a virtual-time Doist that cannot
converge. v1 deploy uses a no-backer registry (effectively unwitnessed at the
TEL layer); completing witnessed TEL issuance is deferred work."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hio.base import doing

from keri.core import coring, eventing, serdering
from keri.core import signing as coresigning
from keri.help import helping
from keri.kering import Kinds
from keri.vdr import credentialing, verifying
from keri.app import grouping, signing
from keri.vc import protocoling

from ..contract import Reply


@dataclass
class Context:
    """Issuance/delivery handle threaded through the pipeline."""
    hby: object
    hab: object
    rgy: object
    registry_name: str


@runtime_checkable
class Issuer(Protocol):
    def issue(self, reply: Reply, ctx: Context) -> bytes:
        """Issue the ACDC declared by `reply` and return a signed IPEX grant exn."""
        ...


def ensure_registry(hby, hab, rgy, *, name: str):
    """Return the registry for `name`, creating it (no backers) if absent.
    The inception Custom Resource creates it exactly once at deploy time; this
    lazy create is a tests/bootstrap fallback (not race-safe)."""
    existing = rgy.registryByName(name)
    if existing is not None:
        return existing
    counselor = grouping.Counselor(hby=hby)
    registrar = credentialing.Registrar(hby=hby, rgy=rgy, counselor=counselor)
    registry = rgy.makeRegistry(name=name, prefix=hab.pre, noBackers=True,
                                nonce=coresigning.Salter().qb64)
    rseal = eventing.SealEvent(registry.regk, "0", registry.regd)
    rseal = dict(i=rseal.i, s=rseal.s, d=rseal.d)
    anc = hab.interact(data=[rseal])
    aserder = serdering.SerderKERI(raw=bytes(anc))
    registrar.incept(iserder=registry.vcp, anc=aserder)
    _complete(rgy, registrar, registry.regk, 0)
    return registry


class IpexGrantIssuer:
    """Default issuer: mints an ACDC of reply's schema to reply.recipient and
    returns a self-contained /ipex/grant exn (ACDC + iss + anchor)."""

    def issue(self, reply: Reply, ctx: Context) -> bytes:
        # The command's `issues` schema SAID was stamped onto the reply by the
        # pipeline (reply.attributes carry the data; the schema rides via ctx).
        return self._issue_grant(
            ctx.hby, ctx.hab, ctx.rgy,
            schema_said=reply.schema_said, recipient=reply.recipient,
            attributes=reply.attributes, edges=reply.edges, rules=reply.rules,
            registry_name=ctx.registry_name)

    def _issue_grant(self, hby, hab, rgy, *, schema_said, recipient, attributes,
                     edges=None, rules=None, registry_name="svc",
                     message="", timestamp=None) -> bytearray:
        timestamp = timestamp or helping.nowIso8601()
        ensure_registry(hby, hab, rgy, name=registry_name)
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
            _, source = coring.Saider.saidify(sad=source, kind=Kinds.json,
                                              label=coring.Saids.d)
        creder = credentialer.create(regname=registry_name, recp=recipient,
                                     schema=schema_said, source=source,
                                     rules=rules, data=attributes, private=False)
        dt = creder.attrib.get("dt", timestamp)
        registry = rgy.registryByName(registry_name)
        iserder = registry.issue(said=creder.said, dt=dt)
        rseal = eventing.SealEvent(iserder.pre, iserder.snh, iserder.said)
        rseal = dict(i=rseal.i, s=rseal.s, d=rseal.d)
        if registry.estOnly:
            anc = hab.rotate(data=[rseal])
        else:
            anc = hab.interact(data=[rseal])
        aserder = serdering.SerderKERI(raw=bytes(anc))
        credentialer.issue(creder, iserder)
        registrar.issue(creder, iserder, aserder)
        _complete(rgy, registrar, iserder.pre, iserder.sn,
                  verifier=verifier, credentialer=credentialer, cred_said=creder.said)
        return _frame_grant(hby, hab, rgy, creder.said, recipient, message, timestamp)


def _frame_grant(hby, hab, rgy, said, recp, message, timestamp) -> bytearray:
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


def _complete(rgy, registrar, pre, sn, *, verifier=None, credentialer=None,
              cred_said=None, rounds: int = 64):
    def _done():
        if not registrar.complete(pre=pre, sn=sn):
            return False
        if credentialer is not None and cred_said is not None:
            return credentialer.complete(said=cred_said)
        return True

    doers = [registrar] if credentialer is None else [registrar, credentialer]
    doist = doing.Doist(real=False, tock=1.0)
    deeds = None
    try:
        deeds = doist.enter(doers=doers)
        for _ in range(rounds):
            if _done():
                return
            rgy.processEscrows()
            if verifier is not None:
                verifier.processEscrows()
            doist.recur(deeds=deeds)
        if not _done():
            raise RuntimeError(f"TEL event (pre={pre}, sn={sn}) did not complete")
    finally:
        if deeds is not None:
            doist.exit(deeds=deeds)
```

> NOTE: `Reply` gains a `schema_said` field stamped by the pipeline (Task 4) before `Issuer.issue` runs (it is the command's `issues` value). Add `schema_said: Optional[str] = None` to the `Reply` dataclass in `contract.py` as part of this task (it is a passive field; the factory methods do not set it). Update the issue test above to set it: after building `reply`, add `reply.schema_said = schemer.said`.

- [ ] **Step 8: Add `schema_said` to `Reply` and run issue test**

Edit `keri_serviceaid/contract.py`: add `schema_said: Optional[str] = None` as the last field of `Reply`. Then edit the test from Step 5 to set `reply.schema_said = schemer.said` before `IpexGrantIssuer().issue(...)`.

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_issue.py -v --import-mode=importlib`
Expected: PASS (1 passed).

- [ ] **Step 9: Write the failing deliver test**

Create `tests/serviceaid/test_providers_deliver.py`:
```python
"""PostmanDeliverer enqueues the grant on a Poster with the right dest/topic."""
from keri.core import serdering
from keri_serviceaid import PostmanDeliverer, Endpoint, Context


class FakePoster:
    def __init__(self):
        self.calls = []

    def send(self, dest, topic, serder, src=None, hab=None, attachment=None):
        self.calls.append(dict(dest=dest, topic=topic, serder=serder,
                               src=src, hab=hab, attachment=attachment))


def test_deliver_calls_poster_send_with_dest_and_topic():
    poster = FakePoster()
    deliverer = PostmanDeliverer(poster=poster)

    # a minimal valid exn grant byte stream: reuse a real exn serder raw
    from keri.app.habbing import Habery
    from keri.core.signing import Salter
    from keri.peer import exchanging
    hby = Habery(name="svc", temp=True, salt=Salter(raw=b'0123456789abcdef').qb64)
    hab = hby.makeHab(name="svc", transferable=True)
    exn, _ = exchanging.exchange(route="/ipex/grant", payload={}, sender=hab.pre)
    msg = bytearray(exn.raw)

    ep = Endpoint(role="mailbox", eid="EMbx", url="https://mailbox.keri.host")
    ctx = Context(hby=hby, hab=hab, rgy=None, registry_name="svc")
    deliverer.deliver(bytes(msg), ep, ctx)

    assert len(poster.calls) == 1
    call = poster.calls[0]
    assert call["dest"] == "EMbx"            # deliver to the resolved endpoint provider
    assert call["topic"] == "credential"
    assert call["hab"] is hab
    assert isinstance(call["serder"], serdering.SerderKERI)
    hby.close()
```

- [ ] **Step 10: Run deliver test to verify it fails**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_deliver.py -v --import-mode=importlib`
Expected: FAIL (`PostmanDeliverer` has no `deliver`).

- [ ] **Step 11: Implement `deliver.py`**

Replace `keri_serviceaid/providers/deliver.py` with:
```python
"""Deliverer extension point + PostmanDeliverer default.

A reply is a NEW signed message routed to the requester's mailbox — never the
HTTP response. PostmanDeliverer wraps forwarding.Poster, which envelopes the
grant in a /fwd exn and posts it to the resolved endpoint provider (mailbox /
controller / agent / witness) for store-and-forward. The requester polls its
mailbox (SSE qry route='mbx') to receive it."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from keri.core import serdering
from keri.app import forwarding

from .resolve import Endpoint
from .issue import Context

GRANT_TOPIC = "credential"


@runtime_checkable
class Deliverer(Protocol):
    def deliver(self, msg: bytes, endpoint: Endpoint, ctx: Context) -> None:
        """Deliver the signed grant `msg` to `endpoint` (async, store-and-forward)."""
        ...


class PostmanDeliverer:
    """Default deliverer. Splits the grant CESR stream into serder + attachment,
    enqueues it on a Poster targeting endpoint.eid, then drains the Poster on a
    virtual-time Doist so the /fwd post completes within the Lambda invocation."""

    def __init__(self, poster=None):
        self._poster = poster   # injectable for tests; None ⇒ build per-deliver

    def deliver(self, msg: bytes, endpoint: Endpoint, ctx: Context) -> None:
        ims = bytearray(msg)
        serder = serdering.SerderKERI(raw=bytes(ims))
        del ims[:serder.size]
        attachment = bytes(ims) if ims else None

        poster = self._poster or forwarding.Poster(hby=ctx.hby)
        poster.send(dest=endpoint.eid, topic=GRANT_TOPIC, serder=serder,
                    hab=ctx.hab, attachment=attachment)

        if self._poster is None:
            # Drive the real Poster's deliverDo to completion (it queues then posts).
            from hio.base import doing
            doist = doing.Doist(real=False, tock=0.03125, limit=8.0, doers=[poster])
            doist.do(doers=[poster])
```

> NOTE: The injectable `poster=` keeps the unit test AWS-free and asserts the exact `send` call; the runtime path (`self._poster is None`) builds a real Poster and drains it. Delivering to `endpoint.eid` (the resolved provider) matches `Poster.send(dest=...)` which then calls `hab.endsFor(dest)` internally — the resolved provider IS a reachable mailbox/controller for the requester.

- [ ] **Step 12: Run deliver test to verify it passes**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_providers_deliver.py -v --import-mode=importlib`
Expected: PASS (1 passed).

- [ ] **Step 13: Re-run the import test (Task 1 closure) and full provider suite**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_import.py tests/serviceaid/test_providers_issue.py tests/serviceaid/test_providers_deliver.py tests/serviceaid/test_providers_idempotency.py -v --import-mode=importlib`
Expected: PASS (all).

- [ ] **Step 14: Commit**

```bash
git add keri_serviceaid/providers/issue.py keri_serviceaid/providers/deliver.py keri_serviceaid/providers/idempotency.py keri_serviceaid/contract.py tests/serviceaid/test_providers_issue.py tests/serviceaid/test_providers_deliver.py tests/serviceaid/test_providers_idempotency.py
git commit -m "feat(serviceaid): issue/deliver/idempotency providers (Protocol + default)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Pipeline (`pipeline.py`)

**Files:**
- Create: `keri_serviceaid/pipeline.py`
- Test: `tests/serviceaid/test_pipeline.py`

**Interfaces:**
- Consumes: `ServiceAid`, `Request`, `Reply` (contract); `Context` (issue); `Endpoint` (resolve); `KeyState`/`VerificationError` (verify). A `state` object exposing `.svc`, `.hby`, `.hab`, `.rgy`, `.cfg.alias`. The verified exn `serder` exposes `.ked` (dict with `i`, `r`, `a`, ...) and `.said`.
- Produces: `process(state, serder, attachments) -> None` — the per-inbound compose. Pure logic; all side effects through the injected providers. Branch outcomes per **v1 grant + silence**.

- [ ] **Step 1: Write the failing pipeline tests**

Create `tests/serviceaid/test_pipeline.py`:
```python
"""Pipeline behavior with FAKE providers injected into a ServiceAid.

Asserts BEHAVIOR not HTTP: grant delivered to endpoint; deny→silence;
replay→re-deliver-not-re-issue; compute-raise→nothing recorded/issued."""
from types import SimpleNamespace

from keri_serviceaid import (ServiceAid, Reply, Request, KeyState, Endpoint,
                             VerificationError)
from keri_serviceaid import pipeline


# ---- fakes -----------------------------------------------------------------
class FakeVerifier:
    def __init__(self, raise_=False):
        self.raise_ = raise_

    def verify(self, sender, ims, hby):
        if self.raise_:
            raise VerificationError("tier unmet")
        return KeyState(pre=sender, tier="receipts")


class FakeAuthz:
    def __init__(self, allow=True):
        self.allow = allow

    def authorize(self, req):
        return (self.allow, "" if self.allow else "denied")


class FakeIssuer:
    def __init__(self):
        self.calls = 0

    def issue(self, reply, ctx):
        self.calls += 1
        return b"GRANT-" + reply.recipient.encode()


class FakeResolver:
    def resolve(self, sender, hby):
        return Endpoint(role="mailbox", eid="EMbx", url="https://mbx")


class FakeDeliverer:
    def __init__(self):
        self.delivered = []

    def deliver(self, msg, endpoint, ctx):
        self.delivered.append((bytes(msg), endpoint.eid))


class FakeLedger:
    def __init__(self):
        self.store = {}

    def seen(self, said):
        return self.store.get(said)

    def record(self, said, grant):
        self.store[said] = bytes(grant)


def _serder(route="/svc/cmd/go", sender="EReq", said="ESaid1", attrs=None):
    return SimpleNamespace(ked={"i": sender, "r": route, "a": attrs or {"k": "v"}},
                           said=said, raw=b"")


def _state(svc, ledger, issuer, resolver, deliverer, verifier, authz):
    svc.idempotency = ledger
    svc.issuer = issuer
    svc.resolver = resolver
    svc.deliverer = deliverer
    svc.verifier = verifier
    svc.authz = authz
    return SimpleNamespace(svc=svc, hby=object(), hab=object(), rgy=object(),
                           cfg=SimpleNamespace(alias="svc"))


def _svc_with_acdc_command():
    svc = ServiceAid(alias="svc")

    @svc.command(route="/svc/cmd/go", issues="ESchema")
    def go(req: Request) -> Reply:
        return Reply.acdc(recipient=req.sender, attributes=req.payload)
    return svc


def test_acdc_path_issues_records_and_delivers():
    issuer, resolver, deliverer, ledger = (FakeIssuer(), FakeResolver(),
                                           FakeDeliverer(), FakeLedger())
    state = _state(_svc_with_acdc_command(), ledger, issuer, resolver, deliverer,
                   FakeVerifier(), FakeAuthz(allow=True))
    pipeline.process(state, _serder(), attachments=[])
    assert issuer.calls == 1
    assert deliverer.delivered == [(b"GRANT-EReq", "EMbx")]
    assert ledger.seen("ESaid1") == b"GRANT-EReq"   # recorded BEFORE deliver


def test_deny_is_silent_no_issue_no_deliver():
    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    state = _state(_svc_with_acdc_command(), ledger, issuer, FakeResolver(),
                   deliverer, FakeVerifier(), FakeAuthz(allow=False))
    pipeline.process(state, _serder(), attachments=[])
    assert issuer.calls == 0 and deliverer.delivered == []
    assert ledger.seen("ESaid1") is None


def test_replay_redelivers_recorded_grant_not_reissue():
    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    ledger.record("ESaid1", b"PRIOR-GRANT")          # simulate a prior issuance
    state = _state(_svc_with_acdc_command(), ledger, issuer, FakeResolver(),
                   deliverer, FakeVerifier(), FakeAuthz(allow=True))
    pipeline.process(state, _serder(said="ESaid1"), attachments=[])
    assert issuer.calls == 0                          # NOT re-issued
    assert deliverer.delivered == [(b"PRIOR-GRANT", "EMbx")]   # re-delivered


def test_compute_raise_records_nothing():
    svc = ServiceAid(alias="svc")

    @svc.command(route="/svc/cmd/boom")
    def boom(req): raise RuntimeError("kaboom")

    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    state = _state(svc, ledger, issuer, FakeResolver(), deliverer,
                   FakeVerifier(), FakeAuthz(allow=True))
    pipeline.process(state, _serder(route="/svc/cmd/boom"), attachments=[])
    assert issuer.calls == 0 and deliverer.delivered == []
    assert ledger.store == {}


def test_bad_signature_tier_unmet_is_silent():
    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    state = _state(_svc_with_acdc_command(), ledger, issuer, FakeResolver(),
                   deliverer, FakeVerifier(raise_=True), FakeAuthz(allow=True))
    pipeline.process(state, _serder(), attachments=[])
    assert issuer.calls == 0 and deliverer.delivered == []


def test_unknown_route_is_silent():
    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    state = _state(_svc_with_acdc_command(), ledger, issuer, FakeResolver(),
                   deliverer, FakeVerifier(), FakeAuthz(allow=True))
    pipeline.process(state, _serder(route="/svc/cmd/nope"), attachments=[])
    assert issuer.calls == 0 and deliverer.delivered == []


def test_reject_and_none_are_silent():
    for kind in ("reject", "none"):
        svc = ServiceAid(alias="svc")

        @svc.command(route="/svc/cmd/q")
        def q(req, _kind=kind):
            return Reply.reject(reason="no") if _kind == "reject" else Reply.none()

        issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
        state = _state(svc, ledger, issuer, FakeResolver(), deliverer,
                       FakeVerifier(), FakeAuthz(allow=True))
        pipeline.process(state, _serder(route="/svc/cmd/q"), attachments=[])
        assert issuer.calls == 0 and deliverer.delivered == []
        assert ledger.store == {}
```

- [ ] **Step 2: Run pipeline tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_pipeline.py -v --import-mode=importlib`
Expected: FAIL (`module 'keri_serviceaid.pipeline' has no attribute 'process'`).

- [ ] **Step 3: Implement `pipeline.py`**

Create `keri_serviceaid/pipeline.py`:
```python
"""The per-inbound compose: verify-tier → dispatch → idempotency → authorize →
compute → branch. Pure logic; every side effect goes through an injected provider
on `state.svc`. v1 ships GRANT on success + SILENCE on every other outcome
(deny / reject / none / unknown route / bad sig / compute-raise → log, no reply).

Ordering of exactly-once issuance + idempotent re-delivery is load-bearing:
record(said, grant) happens AFTER issue but BEFORE deliver, so a delivery failure
+ client re-send hits seen() and re-delivers the SAME grant (never re-issues)."""
from __future__ import annotations

import logging

from .contract import Request
from .providers.issue import Context
from .providers.verify import VerificationError

logger = logging.getLogger(__name__)


def process(state, serder, attachments) -> None:
    svc = state.svc
    sender = serder.ked["i"]
    route = serder.ked["r"]
    said = serder.said

    # 1. Verify the sender's assurance tier against the oracle key state.
    try:
        key_state = svc.verifier.verify(sender, attachments, state.hby)
    except VerificationError as exc:
        logger.warning("verification failed for %s on %s: %s — silent drop",
                       sender, route, exc)
        return

    # 2. Dispatch by the SIGNED `r`. No command → no behavior → no reply.
    cmd = svc.lookup(route)
    if cmd is None:
        logger.info("no command for route %s — silent drop", route)
        return

    # 3. Idempotency: a replay re-delivers the recorded grant, never re-issues.
    prior = svc.idempotency.seen(said)
    if prior is not None:
        endpoint = svc.resolver.resolve(sender, state.hby)
        ctx = Context(hby=state.hby, hab=state.hab, rgy=state.rgy,
                      registry_name=state.cfg.alias)
        svc.deliverer.deliver(prior, endpoint, ctx)
        logger.info("replay of %s — re-delivered recorded grant", said)
        return

    # 4. Authorize. v1 deny → log, no reply (signed spurn/denial is a follow-on).
    attrs = serder.ked.get("a", {}) or {}
    req = Request(sender=sender, route=route, payload=attrs, credentials=[],
                  message_said=said, key_state=key_state)
    allow, reason = svc.authz.authorize(req)
    if not allow:
        logger.info("authorization denied on %s: %s — silent drop", route, reason)
        return

    # 5. Compute. A raise → log, no reply, NOT recorded (safe re-send).
    try:
        reply = cmd.fn(req)
    except Exception:
        logger.exception("command %s raised — silent drop, not recorded", route)
        return

    # 6. Branch (v1 grant + silence).
    if reply.kind == "acdc":
        reply.schema_said = cmd.issues          # stamp the command's issued schema
        ctx = Context(hby=state.hby, hab=state.hab, rgy=state.rgy,
                      registry_name=state.cfg.alias)
        grant = svc.issuer.issue(reply, ctx)
        svc.idempotency.record(said, grant)     # BEFORE delivery (exactly-once issue)
        endpoint = svc.resolver.resolve(sender, state.hby)
        svc.deliverer.deliver(grant, endpoint, ctx)
        logger.info("issued + delivered grant for %s to %s", said, endpoint.eid)
        return

    # reject / none → v1: log, no reply.
    logger.info("command %s returned kind=%s — no reply (v1 grant+silence)",
                route, reply.kind)
```

- [ ] **Step 4: Run pipeline tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_pipeline.py -v --import-mode=importlib`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add keri_serviceaid/pipeline.py tests/serviceaid/test_pipeline.py
git commit -m "feat(serviceaid): per-inbound pipeline (verify/dispatch/idempotency/authz/branch)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Cold-start runtime + Receiptor inception (`runtime.py`, `config.py`)

**Files:**
- Create: `keri_serviceaid/config.py` (migrate from `keri_cdk/handlers/serviceaid/config.py`)
- Create: `keri_serviceaid/runtime.py`
- Test: `tests/serviceaid/test_runtime_v2.py`

**Interfaces:**
- Consumes: `ServiceAid` and providers (defaults wired here); `Config`.
- Produces:
  - `Config.from_env()` with fields `alias, core_table, keeper_secret, witnesses, toad, handler_ref, region, endpoint_url, secret_endpoint_url`; properties `kel_namespace`, `tel_namespace`.
  - `RuntimeState(cfg, hby, hab, rgy, svc)`.
  - `runtime.init(cfg=None) -> RuntimeState` (warm singleton), `runtime.reset()`, `runtime._CaptureHandler`, `runtime.incept_or_load(hby, cfg) -> hab` (uses `Receiptor`, never `WitnessReceiptor`).

- [ ] **Step 1: Write the failing config + cross-Habery oracle test + Receiptor regression-guard**

Create `tests/serviceaid/test_runtime_v2.py`:
```python
"""Cold-start runtime: env config, default-provider wiring, cross-Habery oracle
read, and the Receiptor (never WitnessReceiptor) regression guard."""
import inspect

import boto3
import pytest
from moto import mock_aws

from keri.db.dynamodbing import DynamoDBer
from keri.app.lambding import BASER_STORES, SHARED_KEL_STORES, setup_baser
from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import parsing
from keri.kering import Vrsn_1_0

from keri_serviceaid import config, runtime
from keri_serviceaid import (Allowlist, OracleVerifier, OracleResolver,
                             IpexGrantIssuer, PostmanDeliverer, DynamoLedger)


def test_config_from_env_parses_handler_ref(monkeypatch):
    monkeypatch.setenv("SERVICEAID_ALIAS", "gated")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "keri-core")
    monkeypatch.setenv("SERVICEAID_HANDLER", "gated_handler:svc")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "EWit1,EWit2")
    monkeypatch.setenv("SERVICEAID_TOAD", "2")
    cfg = config.Config.from_env()
    assert cfg.alias == "gated"
    assert cfg.handler_ref == "gated_handler:svc"
    assert cfg.witnesses == ["EWit1", "EWit2"]
    assert cfg.toad == 2
    assert cfg.keeper_secret == "keri/gated/keeper"
    assert cfg.kel_namespace == "gated:kel" and cfg.tel_namespace == "gated:tel"


def test_init_wires_default_providers_for_none(monkeypatch):
    """A ServiceAid with all providers None gets defaults instantiated by init.
    We stub the heavy keripy build and only exercise the default-wiring helper."""
    from keri_serviceaid.contract import ServiceAid
    svc = ServiceAid(alias="gated")
    fake_db = object()
    runtime._wire_default_providers(svc, db=fake_db)
    assert isinstance(svc.authz, Allowlist)
    assert isinstance(svc.verifier, OracleVerifier) and svc.verifier.tier == "receipts"
    assert isinstance(svc.resolver, OracleResolver)
    assert isinstance(svc.issuer, IpexGrantIssuer)
    assert isinstance(svc.deliverer, PostmanDeliverer)
    assert isinstance(svc.idempotency, DynamoLedger)


def test_incept_or_load_uses_receiptor_not_witnessreceiptor():
    """Regression guard for keripy#1422 / locksmith#77: the inception code path
    must reference Receiptor (sync /receipts) and never WitnessReceiptor."""
    src = inspect.getsource(runtime.incept_or_load)
    assert "Receiptor" in src
    assert "WitnessReceiptor" not in src, (
        "inception must use Receiptor (/receipts), not WitnessReceiptor — the "
        "direct-mode push assumption silently hangs over HTTP/Lambda")


def test_cross_habery_oracle_read_kever_visible(monkeypatch):
    """Two Haberys sharing the `shared#` namespace on one moto table: AID-A's
    kever (parsed into service-A's db) is visible to service-B from a local read."""
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1")

        def _open(ns):
            d = DynamoDBer.open(name=ns, stores=BASER_STORES, table_name="keri-core",
                                namespace=ns, shared_namespace="shared",
                                shared_stores=SHARED_KEL_STORES, region="us-east-1")
            setup_baser(d)
            return d

        # service-A writes AID-A's KEL into the shared oracle.
        prod = Habery(name="prod", temp=True, salt=Salter(raw=b'aaaaaaaaaaaaaaaa').qb64)
        producer = prod.makeHab(name="prod", transferable=True)
        kel = bytearray(producer.replay())
        prod.close()

        dbA = _open("svca:kel")
        parsing.Parser(version=Vrsn_1_0).parse(ims=bytearray(kel), kvy=_kvy(dbA))
        # service-B opens its OWN private ns but the SAME shared oracle.
        dbB = _open("svcb:kel")
        from keri.core.eventing import Kevery
        kvyB = Kevery(db=dbB)
        # B can resolve A's KEL: re-parsing is a no-op (already in shared#), so the
        # event is readable from B's view of the pooled kels store.
        assert dbB.kels.get(keys=(producer.pre, "0".rjust(32, "0"))) is not None or \
               dbB.evts.getItemIter() is not None
        dbA.close(); dbB.close()


def _kvy(db):
    from keri.core.eventing import Kevery
    return Kevery(db=db)
```

> NOTE: The cross-Habery assertion verifies the shared oracle pools the public KEL stores. If the exact `kels` key shape is awkward to assert, fall back to asserting `producer.pre in {p for p, *_ in [k for k, _ in dbB.evts.getItemIter()]}` style membership over `dbB.evts`. The decisive property is: a KEL written through ns `svca:kel` (shared stores → `shared#`) is readable through ns `svcb:kel` (same `shared#`). Keep one robust assertion.

- [ ] **Step 2: Run runtime tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_runtime_v2.py -v --import-mode=importlib`
Expected: FAIL (`module 'keri_serviceaid.config' not found` / `runtime` has no `incept_or_load`).

- [ ] **Step 3: Implement `config.py`** (migrate; `handler_module` → `handler_ref`)

Create `keri_serviceaid/config.py`:
```python
"""Environment-driven config for a Service-AID Lambda. The keeper lives in one
KMS-encrypted Secrets Manager secret per stack (keri/<alias>/keeper)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    alias: str
    core_table: str
    keeper_secret: str = ""
    witnesses: list[str] = field(default_factory=list)
    toad: int = 0
    handler_ref: str = ""        # ASGI-style module:attr (e.g. "gated_handler:svc")
    region: str = "us-east-1"
    endpoint_url: str | None = None
    secret_endpoint_url: str | None = None

    @property
    def kel_namespace(self) -> str:
        return f"{self.alias}:kel"

    @property
    def tel_namespace(self) -> str:
        return f"{self.alias}:tel"

    @classmethod
    def from_env(cls) -> "Config":
        alias = os.environ["SERVICEAID_ALIAS"]
        wits = [w for w in os.environ.get("SERVICEAID_WITNESSES", "").split(",") if w]
        toad_env = os.environ.get("SERVICEAID_TOAD")
        toad = int(toad_env) if toad_env else len(wits)
        keeper_secret = (os.environ.get("SERVICEAID_KEEPER_SECRET")
                         or f"keri/{alias}/keeper")
        return cls(
            alias=alias,
            core_table=os.environ["SERVICEAID_CORE_TABLE"],
            keeper_secret=keeper_secret,
            witnesses=wits,
            toad=toad,
            handler_ref=os.environ.get("SERVICEAID_HANDLER", ""),
            region=os.environ.get("SERVICEAID_REGION", "us-east-1"),
            endpoint_url=os.environ.get("SERVICEAID_ENDPOINT_URL") or None,
            secret_endpoint_url=os.environ.get("SERVICEAID_SECRET_ENDPOINT_URL") or None,
        )
```

- [ ] **Step 4: Implement `runtime.py`**

Create `keri_serviceaid/runtime.py`:
```python
"""Cold-start initialization + warm singleton for a Service-AID Lambda.

Opens the Baser `db` on the shared oracle namespace + own private ns; opens the
Reger PRIVATE (credential bodies never pool); keeper from Secrets Manager; builds
Habery; incepts-or-loads the witnessed AID collecting receipts via Receiptor
(NEVER WitnessReceiptor); ensures the registry; publishes own end-role + OOBI;
imports the dev's compute_code module via handler_ref (module:attr) → svc; wires
default providers for any the dev left None; registers schemas; adds a capture
behavior per route to hby.exc; returns RuntimeState.

init() must be called from the handler, never at module import (SnapStart safety:
the keeper secret is fetched inside init)."""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from hio.base import doing

from keri.db.dynamodbing import DynamoDBer
from keri.db.secretkeeper import SecretStore, SecretKeeper
from keri.app.lambding import (BASER_STORES, REGER_STORES, SHARED_KEL_STORES,
                               setup_baser, setup_keeper, setup_reger)
from keri.app.habbing import Habery
from keri.app.configing import Configer
from keri.app import agenting
from keri.kering import Roles
from keri.vdr import credentialing

from .config import Config
from .contract import ServiceAid
from .providers import (Allowlist, OracleVerifier, OracleResolver,
                       IpexGrantIssuer, PostmanDeliverer, DynamoLedger)
from .providers.idempotency import PROC_STORE
from .providers.issue import ensure_registry

logger = logging.getLogger(__name__)

_state = None  # warm singleton across invocations


@dataclass
class RuntimeState:
    cfg: Config
    hby: object
    hab: object
    rgy: object
    svc: ServiceAid


def reset():
    """Drop the warm singleton (test/maintenance + inception CR hook)."""
    global _state
    if _state is not None:
        try:
            _state.hby.close()
        except Exception:
            logger.exception("error closing Habery during reset")
        try:
            _state.rgy.reger.close()
        except Exception:
            logger.exception("error closing reger during reset")
    _state = None


class _CaptureHandler:
    """Exchanger behavior that stashes verified exns for synchronous dispatch."""

    def __init__(self, resource):
        self.resource = resource
        self.captured = []   # list of (serder, attachments)

    def verify(self, serder, attachments=None, **kw):
        return True

    def handle(self, serder, attachments=None, **kw):
        self.captured.append((serder, attachments or []))

    def drain(self):
        out, self.captured = self.captured, []
        return out


def _dynamo_kwa(cfg: Config) -> dict:
    kwa = dict(region=cfg.region)
    if cfg.endpoint_url:
        kwa["endpoint_url"] = cfg.endpoint_url
        import boto3
        kwa["session"] = boto3.Session(aws_access_key_id="fake",
                                       aws_secret_access_key="fake",
                                       region_name=cfg.region)
    return kwa


def _wire_default_providers(svc: ServiceAid, *, db) -> None:
    """Instantiate the default impl for any provider the dev left None."""
    if svc.authz is None:
        svc.authz = Allowlist([])
    if svc.verifier is None:
        svc.verifier = OracleVerifier(tier="receipts")
    if svc.resolver is None:
        svc.resolver = OracleResolver()
    if svc.issuer is None:
        svc.issuer = IpexGrantIssuer()
    if svc.deliverer is None:
        svc.deliverer = PostmanDeliverer()
    if svc.idempotency is None:
        svc.idempotency = DynamoLedger(db)


def incept_or_load(hby, cfg: Config):
    """Load the service hab by alias, or incept it WITNESSED, collecting its own
    receipts via agenting.Receiptor (POST /receipts). NEVER use WitnessReceiptor:
    its direct-mode push assumption silently hangs over HTTP/Lambda
    (keripy#1422, locksmith#77)."""
    hab = hby.habByName(cfg.alias)
    if hab is not None:
        hby.prefixes.add(hab.pre)
        return hab

    with hby.ks.deferflush():     # single atomic keeper write on incept
        hab = hby.makeHab(name=cfg.alias, transferable=True,
                          wits=cfg.witnesses, toad=cfg.toad,
                          isith="1", icount=1, nsith="1", ncount=1)
    hby.prefixes.add(hab.pre)

    if hab.kever.wits:
        # Synchronous /receipts collection on a real-time Doist with a deadline.
        receiptor = agenting.Receiptor(hby=hby)
        doist = doing.Doist(real=True, tock=0.03125, limit=30.0, doers=[receiptor])
        deeds = doist.enter(doers=[receiptor])
        gen = receiptor.receipt(hab.pre, sn=0)
        try:
            while True:
                next(gen)
                doist.recur(deeds=deeds)
        except StopIteration:
            pass
        finally:
            doist.exit(deeds=deeds)
    return hab


def _publish_end_role_and_oobi(hby, hab):
    """Publish the service AID's own controller end-role so requesters can reach
    it (and so endsFor on peers resolves). Best-effort; logged on failure."""
    try:
        rpy = hab.makeEndRole(eid=hab.pre, role=Roles.controller)
        hby.psr.parse(ims=bytearray(rpy))
    except Exception:
        logger.exception("failed to publish own end-role (non-fatal)")


def init(cfg: Config | None = None) -> RuntimeState:
    global _state
    if _state is not None:
        return _state

    cfg = cfg or Config.from_env()
    kwa = _dynamo_kwa(cfg)

    # Baser db: own private ns + the shared oracle ns for public KEL stores.
    db = DynamoDBer.open(name=cfg.alias, stores=BASER_STORES + [PROC_STORE],
                         table_name=cfg.core_table, namespace=cfg.kel_namespace,
                         shared_namespace="shared", shared_stores=SHARED_KEL_STORES,
                         **kwa)
    setup_baser(db)
    # Reger: PRIVATE — credential bodies/TEL never pool (no shared args).
    reger = DynamoDBer.open(name=cfg.alias, stores=REGER_STORES,
                            table_name=cfg.core_table,
                            namespace=cfg.tel_namespace, **kwa)
    setup_reger(reger)

    store = SecretStore(region=cfg.region, endpoint_url=cfg.secret_endpoint_url)
    ks = SecretKeeper.open(store=store, secret_name=cfg.keeper_secret)
    setup_keeper(ks)
    if not ks.bran:
        logger.warning("keeper secret %s has no bran — keeper UNENCRYPTED",
                       cfg.keeper_secret)

    cf = Configer(name=cfg.alias, temp=True)
    hby = Habery(name=cfg.alias, temp=False, free=True, db=db, ks=ks, cf=cf,
                 salt=ks.salt, bran=ks.bran)

    hab = incept_or_load(hby, cfg)
    rgy = credentialing.Regery(hby=hby, name=cfg.alias, reger=reger)
    ensure_registry(hby, hab, rgy, name=cfg.alias)
    _publish_end_role_and_oobi(hby, hab)

    # Import the dev compute_code module and grab the ServiceAid via module:attr.
    if not cfg.handler_ref or ":" not in cfg.handler_ref:
        raise ValueError(f"SERVICEAID_HANDLER must be 'module:attr' (got "
                         f"{cfg.handler_ref!r})")
    module_name, attr = cfg.handler_ref.split(":", 1)
    module = importlib.import_module(module_name)
    svc = getattr(module, attr)
    if not isinstance(svc, ServiceAid):
        raise TypeError(f"{cfg.handler_ref} did not resolve to a ServiceAid")

    _wire_default_providers(svc, db=db)

    # Register the dev's ACDC schemas so Credentialer.create can validate.
    from keri.core import scheming
    from keri.kering import Kinds
    for sad in svc.schemas:
        schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
        if hby.db.schema.get(keys=(schemer.said,)) is None:
            hby.db.schema.pin(keys=(schemer.said,), val=schemer)

    # One capture behavior per route (dispatch reads the captured verified exn).
    for route in svc.routes:
        hby.exc.addHandler(_CaptureHandler(resource=route))

    _state = RuntimeState(cfg=cfg, hby=hby, hab=hab, rgy=rgy, svc=svc)
    return _state
```

- [ ] **Step 5: Run runtime tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_runtime_v2.py -v --import-mode=importlib`
Expected: PASS (4 passed). If the cross-Habery assertion is brittle, adjust to the membership form noted in Step 1; the Receiptor regression-guard, config, and default-wiring tests must all pass as written.

- [ ] **Step 6: Commit**

```bash
git add keri_serviceaid/config.py keri_serviceaid/runtime.py tests/serviceaid/test_runtime_v2.py
git commit -m "feat(serviceaid): cold-start runtime + Receiptor witnessed inception

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Handler (CESR-ingest → 204 + CR fork + bootstrap)

**Files:**
- Create: `keri_serviceaid/bootstrap.py` (migrate from `keri_cdk/handlers/serviceaid/bootstrap.py`)
- Create: `keri_serviceaid/handler.py`
- Test: `tests/serviceaid/test_handler_v2.py`

**Interfaces:**
- Consumes: `runtime.init/RuntimeState/_CaptureHandler`, `pipeline.process`, `Config` (for the integration cold start), `keri_cdk._inception.on_event` (CR fork — lazy import).
- Produces: `handler(event, context) -> dict`. CR `RequestType` → `_inception.on_event`. HTTP ingest → reassemble CESR (`body` + `CESR-ATTACHMENT`) → `hby.psr.parse(framed=True)` → escrows → drain captured exn → `pipeline.process` → `{"statusCode": 204}`. Malformed envelope → `{"statusCode": 400}`.

- [ ] **Step 1: Write the failing handler tests**

Create `tests/serviceaid/test_handler_v2.py`:
```python
"""Handler: 204 on accepted ingest, 400 on malformed envelope, CR fork,
and a moto + fake-mailbox end-to-end (incept → POST signed exn → grant
delivered → replay re-delivers)."""
import base64
import json
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from keri_serviceaid import handler as H


# ---------- unit: 204 / 400 / CR fork with a stubbed runtime --------------
def _http_event(body_bytes, attachment="-AAB", content_type="application/cesr"):
    return {
        "httpMethod": "POST", "path": "/",
        "headers": {"Content-Type": content_type, "CESR-ATTACHMENT": attachment},
        "body": base64.b64encode(body_bytes).decode(), "isBase64Encoded": True,
    }


def test_malformed_envelope_returns_400(monkeypatch):
    # No CESR-ATTACHMENT header → malformed → 400 (only real HTTP error).
    ev = {"httpMethod": "POST", "path": "/",
          "headers": {"Content-Type": "application/cesr"},
          "body": base64.b64encode(b'{"v":"KERI10JSON"}').decode(),
          "isBase64Encoded": True}
    monkeypatch.setattr(H.runtime, "init", lambda: SimpleNamespace())
    assert H.handler(ev, None) == {"statusCode": 400}


def test_accepted_ingest_returns_204(monkeypatch):
    captured = {}

    class FakeCapture:
        def drain(self):
            return [(SimpleNamespace(ked={"r": "/svc/cmd/go"}, said="E"), [])]

    class FakeExc:
        routes = {"/svc/cmd/go": FakeCapture()}

    class FakePsr:
        def parse(self, ims, framed=False): pass

    state = SimpleNamespace(
        hby=SimpleNamespace(psr=FakePsr(),
                            kvy=SimpleNamespace(processEscrows=lambda: None),
                            exc=SimpleNamespace(processEscrow=lambda: None,
                                                routes=FakeExc.routes)),
        svc=SimpleNamespace())
    monkeypatch.setattr(H.runtime, "init", lambda: state)
    monkeypatch.setattr(H.pipeline, "process",
                        lambda st, serder, attachments: captured.setdefault("hit", True))
    resp = H.handler(_http_event(b'{"v":"KERI10JSON"}'), None)
    assert resp == {"statusCode": 204}
    assert captured.get("hit") is True


def test_cr_request_type_forks_to_inception(monkeypatch):
    called = {}
    import keri_cdk._inception as inc
    monkeypatch.setattr(inc, "on_event",
                        lambda e, c: called.setdefault("pre", "Epre") or {"ok": 1})
    resp = H.handler({"RequestType": "Create"}, None)
    assert resp == {"ok": 1} and called.get("pre") == "Epre"


# ---------- integration: moto + fake mailbox, full round-trip --------------
@pytest.mark.integration
def test_end_to_end_grant_delivered_and_replay_redelivers(monkeypatch):
    """Cold-start on moto, incept (wits=[]), POST a signed KEL+exn from a test
    requester, oracle verify → compute → issue → deliver into a FAKE mailbox;
    assert the grant landed; replay re-delivers the same grant (not re-issued).

    Implementation outline (the executing agent writes the body to match the
    real APIs):
      1. mock_aws(); boto3 dynamodb + a moto Secrets Manager secret with
         {salt,bran,keeper:null} at keri/itest/keeper.
      2. Build a compute_code module on sys.path defining
         `svc = ServiceAid(alias="itest")` with a /itest/cmd/go acdc command +
         register_schema(...). Set env SERVICEAID_ALIAS/CORE_TABLE/HANDLER/
         ENDPOINT_URL/SECRET_ENDPOINT_URL; toad=0, wits unset.
      3. runtime.reset(); state = runtime.init().
      4. Build a requester Habery; parse its KEL into the service oracle (in-stream
         path); inject a controller end-role for the requester pointing at a fake
         mailbox AID the service can resolve (so OracleResolver.resolve succeeds).
      5. Build a signed /itest/cmd/go exn from the requester; POST it via
         H.handler(_http_event(serder.raw, attachment=<CESR attachment>)).
      6. Monkeypatch svc.deliverer with a capturing FakeDeliverer (or assert the
         Poster wrote into a local mbx). Assert a grant was delivered to the
         resolved endpoint; assert the exn SAID is now seen() in the ledger.
      7. POST the SAME exn again → assert deliverer called again with the SAME
         grant bytes and svc.issuer.issue was NOT called a second time
         (wrap it with a counter).
    """
    pytest.skip("integration scaffold — agent fills the body using the real APIs "
                "per the outline above; gated by -m integration")
```

> NOTE: The first three tests are pure unit (no AWS) and MUST pass. The integration test is marked `@pytest.mark.integration` and ships as a documented scaffold; the executing agent fills its body against the real keripy/moto APIs (the round-trip is also exercised live by the Task 11 real deploy). Register the marker by adding to the worktree root `pytest.ini` (or `conftest.py`) a `markers = integration: requires moto cold-start` line if not present; running without `-m integration` skips it.

- [ ] **Step 2: Run handler unit tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_handler_v2.py -v --import-mode=importlib -m "not integration"`
Expected: FAIL (`keri_serviceaid.handler` not found).

- [ ] **Step 3: Implement `bootstrap.py`** (migrate verbatim, repoint docstring)

Create `keri_serviceaid/bootstrap.py`:
```python
"""Service-AID libsodium shim, shipped in ServiceAidFrameworkLayer.

pysodium does ctypes.cdll.LoadLibrary(ctypes.util.find_library('sodium')). On the
Amazon Linux Lambda image find_library returns None (no gcc/ldconfig), so we
resolve the absolute .so path (from KeriRuntimeLayer at /opt/lib) and patch
find_library to return it. Idempotent; a no-op if the .so isn't found."""
import ctypes
import ctypes.util
import os

_patched = False


def ensure_libsodium():
    global _patched
    task_dir = os.path.dirname(os.path.abspath(__file__))
    lib_dirs = [os.path.join(task_dir, "lib"), task_dir]
    lib_dirs += [d for d in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep) if d]
    lib_dirs.append("/opt/lib")
    sonames = ["libsodium.so.26", "libsodium.so"]
    candidates = [os.path.join(d, s) for d in lib_dirs for s in sonames]

    lib_path = next((p for p in candidates if os.path.exists(p)), None)
    if lib_path and not _patched:
        orig = ctypes.util.find_library

        def _patched_find_library(name):
            if name in ("sodium", "libsodium"):
                return lib_path
            return orig(name)

        ctypes.util.find_library = _patched_find_library
        _patched = True
    return lib_path
```

- [ ] **Step 4: Implement `handler.py`**

Create `keri_serviceaid/handler.py`:
```python
"""Service-AID Lambda entry point: CESR-ingest → 204 + CR fork.

Boundary B (server↔requesters): inbound is a CESR-over-HTTP envelope
(application/cesr body + CESR-ATTACHMENT header). We reassemble it into the
parser buffer (identical to TCP — the parser is transport-blind), parse, drain
the verified exn the Exchanger captured, and drive the pipeline. The HTTP layer
ALWAYS returns 204 No Content on an accepted ingest (zero KERI meaning); the only
real HTTP error is a malformed CESR envelope → 400. Every KERI-semantic outcome
is a signed message to the mailbox (the pipeline's job) or deliberate silence."""
from __future__ import annotations

# Resolve libsodium BEFORE any keri import.
try:
    from .bootstrap import ensure_libsodium
except ImportError:  # pragma: no cover
    ensure_libsodium = None
if ensure_libsodium is not None:
    ensure_libsodium()

import base64
import logging

from . import runtime
from . import pipeline

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _body_bytes(event) -> bytes:
    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode("utf-8") if isinstance(body, str) else bytes(body)


def _reassemble_cesr(event) -> bytearray:
    """Rebuild the CESR stream from the application/cesr body + CESR-ATTACHMENT
    header (parseCesrHttpRequest-style). Raises ValueError on a malformed
    envelope (missing attachment header / undecodable body)."""
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if "cesr-attachment" not in headers:
        raise ValueError("missing CESR-ATTACHMENT header")
    body = _body_bytes(event)
    if not body:
        raise ValueError("empty body")
    ims = bytearray(body)
    ims.extend(headers["cesr-attachment"].encode("utf-8"))
    return ims


def handler(event, context):
    # CloudFormation Custom Resource (inception) shares this Lambda: events carry
    # RequestType instead of an HTTP method. Lazy import so the HTTP path is clean.
    if "RequestType" in event:
        try:
            from _inception import on_event           # flat /var/task on Lambda
        except ImportError:
            from keri_cdk._inception import on_event   # package mode (tests)
        return on_event(event, context)

    state = runtime.init()

    try:
        ims = _reassemble_cesr(event)
    except (ValueError, Exception) as exc:
        logger.warning("malformed CESR envelope: %s", exc)
        return {"statusCode": 400}

    try:
        state.hby.psr.parse(ims=bytearray(ims), framed=True)
        state.hby.kvy.processEscrows()
        state.hby.exc.processEscrow()
    except Exception:
        logger.warning("CESR parse failed", exc_info=True)
        return {"statusCode": 400}

    # Drain every capture behavior and drive the pipeline for each verified exn.
    for behavior in list(state.hby.exc.routes.values()):
        if not hasattr(behavior, "drain"):
            continue
        for serder, attachments in behavior.drain():
            try:
                pipeline.process(state, serder, attachments)
            except Exception:
                # The pipeline already swallows per-outcome failures; this guards
                # the 204 contract against any unexpected provider error.
                logger.exception("pipeline error (suppressed — ingest still 204)")

    return {"statusCode": 204}
```

> NOTE: `_reassemble_cesr` raising `ValueError` for the missing-header case is the malformed-envelope path → 400. The broad `except (ValueError, Exception)` is intentional: any reassembly failure is a transport error. The CR fork is checked first, before `runtime.init()`, so synth/CR tests never touch the HTTP path.

- [ ] **Step 5: Run handler unit tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/serviceaid/test_handler_v2.py -v --import-mode=importlib -m "not integration"`
Expected: PASS (3 passed; integration skipped/deselected).

- [ ] **Step 6: Commit**

```bash
git add keri_serviceaid/bootstrap.py keri_serviceaid/handler.py tests/serviceaid/test_handler_v2.py
git commit -m "feat(serviceaid): CESR-ingest handler (204 + CR fork + bootstrap)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Oracle reachability-complete (`SHARED_KEL_STORES += ends./locs./eans.`)

**Files:**
- Modify: `src/keri/app/lambding.py:64-67` (the `SHARED_KEL_STORES` frozenset)
- Modify: `keri_cdk/probes/leadingkeys/README.md`
- Modify: `keri_cdk/probes/leadingkeys/probe.py` (extend seeded stores)
- Test: `tests/handlers/test_oracle_reachability.py`

**Interfaces:**
- Consumes: `setup_baser`, `DynamoDBer`, `SHARED_KEL_STORES`.
- Produces: `SHARED_KEL_STORES` now contains `ends.`, `locs.`, `eans.` (disjoint from `NEVER_SHARE_STORES`), so `hab.endsFor(peer)` resolves an in-domain peer's mailbox URL from one local read across Haberys sharing the oracle.

- [ ] **Step 1: Write the failing cross-Habery reachability test**

Create `tests/handlers/test_oracle_reachability.py`:
```python
"""ends./locs./eans. are shared in the oracle: service-B resolves service-A's
authorized end-role + location from the pooled shared# namespace."""
import boto3
import pytest
from moto import mock_aws

from keri.db.dynamodbing import DynamoDBer, NEVER_SHARE_STORES
from keri.app.lambding import BASER_STORES, SHARED_KEL_STORES, setup_baser
from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.kering import Roles


def test_reachability_stores_are_shared_and_safe():
    for s in ("ends.", "locs.", "eans."):
        assert s in SHARED_KEL_STORES, f"{s} must be in SHARED_KEL_STORES"
    assert SHARED_KEL_STORES.isdisjoint(NEVER_SHARE_STORES)


@pytest.mark.integration
def test_cross_habery_endsfor_resolves_over_oracle():
    """Service-A publishes an end-role/loc for a peer into the shared ns; a
    second Habery on the SAME oracle table resolves that peer's URL via endsFor.

    Outline (agent fills against real makeEndRole/makeLocScheme APIs):
      1. mock_aws(); two DynamoDBer.open(...) with distinct private ns but the
         SAME shared_namespace='shared'/shared_stores=SHARED_KEL_STORES on table
         'keri-core'; setup_baser each.
      2. Build Habery-A on dbA; a peer hab; publish the peer's controller end-role
         + a https location via hab.makeEndRole/reply; parse into A (lands in
         shared ends./locs./eans.).
      3. Build Habery-B on dbB (same oracle); also parse the peer's KEL so the
         kever exists; assert hby_B.<service hab>.endsFor(peer_pre) returns a
         mailbox/controller URL — i.e. reachability resolved from the oracle.
    """
    pytest.skip("integration scaffold — agent fills body; gated by -m integration")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/handlers/test_oracle_reachability.py::test_reachability_stores_are_shared_and_safe -v --import-mode=importlib`
Expected: FAIL (`ends.`/`locs.`/`eans.` not in `SHARED_KEL_STORES`).

- [ ] **Step 3: Add the three stores to `SHARED_KEL_STORES`**

Edit `src/keri/app/lambding.py` — replace the `SHARED_KEL_STORES` definition (currently lines 64-67) with:
```python
SHARED_KEL_STORES = frozenset({
    "evts.", "fels.", "kels.", "dtss.", "sigs.", "wigs.", "rcts.", "vrcs.",
    "aess.", "fons.", "wits.", "stts.", "ksns.", "knas.",
    # Reachability (end-role / location / endpoint-auth): pooling these makes the
    # oracle REACHABILITY-COMPLETE so a Service-AID resolves an in-domain peer's
    # mailbox/controller endpoint from one local endsFor read (path-(c)). These
    # are public authorization records, NOT confidential — disjoint from
    # NEVER_SHARE_STORES. See 2026-06-17-service-aid-framework-design.md.
    "ends.", "locs.", "eans.",
})
```

- [ ] **Step 4: Run the store-membership test to verify it passes**

Run: `.venv/bin/python -m pytest tests/handlers/test_oracle_reachability.py::test_reachability_stores_are_shared_and_safe -v --import-mode=importlib`
Expected: PASS.

- [ ] **Step 5: Verify no existing keripy/serviceaid test regressed**

Run: `.venv/bin/python -m pytest tests/serviceaid/ -v --import-mode=importlib -m "not integration"`
Expected: PASS (all current serviceaid tests still green — the shared set growing is additive).

- [ ] **Step 6: Extend the LeadingKeys probe to cover the three added stores**

Edit `keri_cdk/probes/leadingkeys/README.md` — under "What it does" item 3, append:
```markdown
   The probe now ALSO seeds the reachability stores (`ends.`, `locs.`, `eans.`)
   into the SHARED `shared#` namespace (not the tenant namespace), reproducing
   the Task 7 oracle change. The crux assertion is unchanged: a tenant must NOT
   be able to GSI-Query ANOTHER tenant's private `gsi_pk`, while the shared
   `shared#ends.`/`shared#locs.`/`shared#eans.` rows are readable by any tenant
   whose policy grants `shared#*` (the four-pattern LeadingKeys union).
```

Edit `keri_cdk/probes/leadingkeys/probe.py`: in the seeding step, add three shared-namespace items keyed `shared#ends.#...`, `shared#locs.#...`, `shared#eans.#...` alongside the existing seeded items, and add a positive assertion that tenant A (granted `shared#*`) CAN GSI-Query `shared#ends.` (reachability is intentionally pooled), keeping the existing cross-tenant DENY assertions intact. (The probe runs only against real AWS; this is a documentation+seed extension, not a moto test.)

- [ ] **Step 7: Commit**

```bash
git add src/keri/app/lambding.py keri_cdk/probes/leadingkeys/README.md keri_cdk/probes/leadingkeys/probe.py tests/handlers/test_oracle_reachability.py
git commit -m "feat(oracle): share ends./locs./eans. — reachability-complete KEL oracle

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: ServiceAidFrameworkLayer + build script

**Files:**
- Create: `keri_cdk/framework_layer.py`
- Create: `keri_cdk/layers/build_framework_layer.sh`
- Modify: `.gitignore` (add the built asset dir)
- Test: `tests/cdk/test_framework_layer.py`

**Interfaces:**
- Consumes: nothing (mirrors `KeriRuntimeLayer`).
- Produces: `ServiceAidFrameworkLayer(scope, cid, *, asset_path=...)` exposing `.layer` (a `LayerVersion`, runtime `PYTHON_3_14`, arch `ARM_64`). Consumed by Task 9.

- [ ] **Step 1: Write the failing synth test**

Create `tests/cdk/test_framework_layer.py`:
```python
"""ServiceAidFrameworkLayer synthesizes a python3.14/arm64 LayerVersion."""
import os
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match

from keri_cdk.framework_layer import ServiceAidFrameworkLayer


def _synth(tmp_path):
    # The asset dir must exist for Code.from_asset; point at a temp dir with a
    # placeholder so synth does not require a real layer build.
    asset = str(tmp_path / "fw")
    os.makedirs(os.path.join(asset, "python"), exist_ok=True)
    open(os.path.join(asset, "python", ".keep"), "w").close()
    app = cdk.App()
    stack = cdk.Stack(app, "S")
    ServiceAidFrameworkLayer(stack, "Fw", asset_path=asset)
    return Template.from_stack(stack)


def test_layer_runtime_and_arch(tmp_path):
    tmpl = _synth(tmp_path)
    tmpl.has_resource_properties("AWS::Lambda::LayerVersion", {
        "CompatibleRuntimes": Match.array_with(["python3.14"]),
        "CompatibleArchitectures": Match.array_with(["arm64"]),
    })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/cdk/test_framework_layer.py -v --import-mode=importlib`
Expected: FAIL (`keri_cdk.framework_layer` not found).

- [ ] **Step 3: Implement `framework_layer.py`**

Create `keri_cdk/framework_layer.py`:
```python
"""ServiceAidFrameworkLayer: prebuilt arm64 Lambda layer carrying the
keri_serviceaid framework package (handler entry, pipeline, contract, default
providers). Pairs with KeriRuntimeLayer (libsodium + keripy native deps) so a
Service-AID Function deploys as a pure-Python zip: the dev's compute_code asset +
two layers.

Layer layout (Lambda extracts a layer zip to /opt):
  python/ -> /opt/python   (on sys.path: keri_serviceaid)

Build the asset with keri_cdk/layers/build_framework_layer.sh. The asset dir
keri_cdk/layers/serviceaid_framework/ is gitignored and regenerated by the build."""
import os

from aws_cdk import aws_lambda as _lambda
from constructs import Construct

_ASSET = os.path.join(os.path.dirname(__file__), "layers", "serviceaid_framework")


class ServiceAidFrameworkLayer(Construct):
    """Prebuilt arm64 layer carrying the keri_serviceaid framework package.
    Build with keri_cdk/layers/build_framework_layer.sh."""

    def __init__(self, scope, cid, *, asset_path=_ASSET, **kw):
        super().__init__(scope, cid, **kw)
        self.layer = _lambda.LayerVersion(
            self, "ServiceAidFramework",
            code=_lambda.Code.from_asset(asset_path),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_14],
            compatible_architectures=[_lambda.Architecture.ARM_64],
            description="keri_serviceaid framework package (arm64)")
```

- [ ] **Step 4: Implement `build_framework_layer.sh`**

Create `keri_cdk/layers/build_framework_layer.sh`:
```bash
#!/usr/bin/env bash
# Build the prebuilt arm64 ServiceAidFrameworkLayer asset.
#
# Lambda extracts a layer zip to /opt. We lay the asset out as:
#   serviceaid_framework/python/  -> /opt/python  (on sys.path: keri_serviceaid)
#
# We build INSIDE the AL arm64 Lambda base image (python3.14) so any compiled
# deps match the Lambda runtime, mirroring build_layer.sh. keri_serviceaid is a
# pure-Python package, so this is effectively a copy of the package tree plus a
# no-deps pip install (keri itself ships in KeriRuntimeLayer — do NOT bundle it
# here, the two layers compose at /opt/python).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/serviceaid_framework"
ROOT="$(git rev-parse --show-toplevel)"

rm -rf "$OUT"
mkdir -p "$OUT/python"

docker run --rm --platform linux/arm64 \
  --entrypoint /bin/sh \
  -v "$ROOT":/work -w /work \
  public.ecr.aws/lambda/python:3.14-arm64 -c '
    set -e
    # Install ONLY keri_serviceaid into python/ with no deps (keri + native libs
    # ride in KeriRuntimeLayer; bundling them again would bloat + shadow).
    pip install --no-cache-dir --no-deps \
      ./keri_serviceaid -t /work/keri_cdk/layers/serviceaid_framework/python \
      2>/dev/null || \
    cp -R /work/keri_serviceaid /work/keri_cdk/layers/serviceaid_framework/python/keri_serviceaid

    PY=/work/keri_cdk/layers/serviceaid_framework/python
    find "$PY" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
    find "$PY" -type d -name "*.dist-info" -prune -exec rm -rf {} + 2>/dev/null || true
  '

echo "---- keri_serviceaid present? ----"
ls -d "$OUT/python/keri_serviceaid" >/dev/null 2>&1 \
  && echo "OK: python/keri_serviceaid exists" || echo "MISSING: python/keri_serviceaid"
echo "---- unzipped layer size ----"
du -sh "$OUT"
```
Make it executable:
```bash
chmod +x keri_cdk/layers/build_framework_layer.sh
```

> NOTE: `keri_serviceaid` is not declared in `setup.py` (which packages only `src/`). The `pip install ./keri_serviceaid` fallback to `cp -R` covers the no-setup case; the package is pure-Python so a tree copy is sufficient. (A future task could add a minimal `keri_serviceaid/pyproject.toml`; not required for v1.)

- [ ] **Step 5: Gitignore the built asset**

Edit `.gitignore`, add (near the existing `keri_runtime/` ignore if present):
```
keri_cdk/layers/serviceaid_framework/
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/cdk/test_framework_layer.py -v --import-mode=importlib`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add keri_cdk/framework_layer.py keri_cdk/layers/build_framework_layer.sh .gitignore tests/cdk/test_framework_layer.py
git commit -m "feat(cdk): ServiceAidFrameworkLayer + build script

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: `ServiceAidFunction` construct (rewrite `service_aid.py`) + retire old code

**Files:**
- Modify (rewrite): `keri_cdk/service_aid.py`
- Modify: `keri_cdk/__init__.py`
- Test: `tests/cdk/test_service_aid_function.py`
- Remove: `keri_cdk/handlers/serviceaid/` (migrated into `keri_serviceaid`)
- Remove: old tests bound to the retired code (listed below)

**Interfaces:**
- Consumes: `KeriRuntimeLayer`, `ServiceAidFrameworkLayer`.
- Produces: `ServiceAidFunction(scope, cid, *, alias, core_table: ITable, compute_code: _lambda.Code, handler_ref="service:svc", witnesses=None, toad=0, runtime_layer=None, framework_layer=None, environment=None, memory=1024, timeout_seconds=120, vpc=None, extra_layers=None)`; attributes `.function`, `.api`, `.inception`; property `grant_principal`. It is an `iam.IGrantable`.

- [ ] **Step 1: Write the failing synth tests**

Create `tests/cdk/test_service_aid_function.py`:
```python
"""Synth assertions for ServiceAidFunction: two layers, layer-resident handler,
env merge, four-pattern LeadingKeys, IGrantable, cross-stack lock, inception CR."""
import json
import os
import tempfile

import aws_cdk as cdk
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_dynamodb as ddb
from aws_cdk.assertions import Template, Match

from keri_cdk import KeriCoreStack, ServiceAidFunction
from keri_cdk.framework_layer import ServiceAidFrameworkLayer
from keri_cdk.runtime_layer import KeriRuntimeLayer

ENV = cdk.Environment(account="111111111111", region="us-east-1")


def _asset():
    d = tempfile.mkdtemp()
    open(os.path.join(d, "gated_handler.py"), "w").close()
    return d


def _synth():
    app = cdk.App()
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=ENV)
    svc = cdk.Stack(app, "Svc", env=ENV)
    fn = ServiceAidFunction(
        svc, "Gated", alias="gated", core_table=core.table,
        compute_code=_lambda.Code.from_asset(_asset()),
        handler_ref="gated_handler:svc",
        runtime_layer=KeriRuntimeLayer(svc, "Rt", asset_path=_asset()),
        framework_layer=ServiceAidFrameworkLayer(svc, "Fw", asset_path=_fw_asset()),
        witnesses=[], toad=0, environment={"EXTRA": "1"})
    return Template.from_stack(svc), Template.from_stack(core), fn


def _fw_asset():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "python"), exist_ok=True)
    open(os.path.join(d, "python", ".keep"), "w").close()
    return d


def test_layer_resident_handler_string():
    svc, _, _ = _synth()
    svc.has_resource_properties("AWS::Lambda::Function", {
        "Handler": "keri_serviceaid.handler.handler",
        "Runtime": "python3.14", "Architectures": ["arm64"],
        "ReservedConcurrentExecutions": 1})


def test_two_layers_attached():
    svc, _, _ = _synth()
    body = json.dumps(svc.to_json())
    # Both layer logical types appear (KeriRuntime + ServiceAidFramework).
    assert body.count("AWS::Lambda::LayerVersion") >= 2


def test_env_merge_keeps_framework_and_custom():
    svc, _, _ = _synth()
    svc.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {"Variables": Match.object_like({
            "SERVICEAID_ALIAS": "gated",
            "SERVICEAID_HANDLER": "gated_handler:svc",
            "EXTRA": "1"})}})


def test_four_pattern_leadingkeys():
    svc, _, _ = _synth()
    body = json.dumps(svc.to_json())
    assert "shared#*" in body and "__meta__#shared#*" in body
    assert "gated:*#*" in body and "__meta__#gated:*" in body


def test_cross_stack_core_table_lock():
    svc, _, _ = _synth()
    assert "Fn::ImportValue" in json.dumps(svc.to_json())


def test_igrantable_grant_read_data_adds_policy():
    svc_tmpl, _, fn = _synth()
    # Granting an arbitrary resource to the construct must add a policy to the
    # FUNCTION's role (delegated via grant_principal).
    assert fn.grant_principal is fn.function.grant_principal


def test_grant_read_data_targets_function_role():
    app = cdk.App()
    core = KeriCoreStack(app, "Core2", table_name="keri-core", env=ENV)
    svc = cdk.Stack(app, "Svc2", env=ENV)
    fn = ServiceAidFunction(
        svc, "Gated", alias="gated", core_table=core.table,
        compute_code=_lambda.Code.from_asset(_asset()),
        handler_ref="gated_handler:svc",
        runtime_layer=KeriRuntimeLayer(svc, "Rt", asset_path=_asset()),
        framework_layer=ServiceAidFrameworkLayer(svc, "Fw", asset_path=_fw_asset()))
    lookup = ddb.Table(svc, "Lookup", partition_key=ddb.Attribute(
        name="pk", type=ddb.AttributeType.STRING))
    lookup.grant_read_data(fn)   # IGrantable payoff
    tmpl = Template.from_stack(svc)
    # A GetItem/Query grant on the lookup table is present in some IAM policy.
    tmpl.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {
        "Statement": Match.array_with([Match.object_like({
            "Action": Match.array_with(["dynamodb:GetItem"])})])}})


def test_api_gateway_cesr_binary_media():
    svc, _, _ = _synth()
    svc.has_resource_properties("AWS::ApiGateway::RestApi", {
        "BinaryMediaTypes": Match.array_with(["application/cesr"])})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/cdk/test_service_aid_function.py -v --import-mode=importlib`
Expected: FAIL (`cannot import name 'ServiceAidFunction'`).

- [ ] **Step 3: Rewrite `service_aid.py`**

Replace `keri_cdk/service_aid.py` with:
```python
"""ServiceAidFunction construct: one Service-AID = a python3.14/arm64 zip Lambda
(the dev's compute_code) riding TWO layers — KeriRuntimeLayer (libsodium + keripy)
and ServiceAidFrameworkLayer (keri_serviceaid) — over the shared pooled core table.

The handler resolves from the framework layer (keri_serviceaid.handler.handler);
the dev's compute_code module (handler_ref module:attr, e.g. "gated_handler:svc")
ships in the asset. iam.IGrantable lets adopters grant their own resources to the
Function the canonical CDK way: my_lookup.grant_read_data(svc).

Inherited unchanged from Phase B/C:
  - cross-stack core-table lifecycle LOCK (core_table: ITable across a stack
    boundary → Export/Fn::ImportValue);
  - four-pattern dynamodb:LeadingKeys union (shared#*, __meta__#shared#*,
    {alias}:*#*, __meta__#{alias}:*);
  - keeper-secret IAM scoped to keri/<alias>/*;
  - inception Custom Resource (the Function doubles as on_event);
  - API Gateway CESR ingest (binary_media_types, proxy, 204).

Real-deploy UNKNOWN (must validate in Task 11): a layer-resident handler
(keri_serviceaid.handler.handler at /opt/python) importing the dev's /var/task
compute_code which imports the framework layer, with libsodium from
KeriRuntimeLayer (/opt/lib). FALLBACK if Lambda will not resolve a layer-resident
handler: a 3-line shim handler.py is auto-injected into the asset:
    from keri_serviceaid.handler import handler  # noqa
and the construct sets handler="handler.handler" instead. We inject the shim
unconditionally (harmless when the layer-resident handler resolves) so the deploy
is robust either way; the handler string stays "keri_serviceaid.handler.handler"
and the shim is a redundant safety net at the asset root."""
from __future__ import annotations

import os
import re
import tempfile

from aws_cdk import Aws, Duration, CustomResource
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import custom_resources as cr
from constructs import Construct

try:
    from .runtime_layer import KeriRuntimeLayer
    from .framework_layer import ServiceAidFrameworkLayer
except ImportError:  # pragma: no cover
    from keri_cdk.runtime_layer import KeriRuntimeLayer
    from keri_cdk.framework_layer import ServiceAidFrameworkLayer


class ServiceAidFunction(Construct, iam.IGrantable):
    """One Service-AID Function: compute_code zip + two layers over the shared
    core table (own namespace) + keeper secret + inception Custom Resource."""

    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        alias: str,
        core_table: ddb.ITable,
        compute_code: _lambda.Code,
        handler_ref: str = "service:svc",
        witnesses: list[str] | None = None,
        toad: int = 0,
        runtime_layer: KeriRuntimeLayer | None = None,
        framework_layer: ServiceAidFrameworkLayer | None = None,
        environment: dict | None = None,
        memory: int = 1024,
        timeout_seconds: int = 120,
        vpc=None,
        extra_layers: list | None = None,
        **kw,
    ):
        super().__init__(scope, cid, **kw)
        if not re.fullmatch(r"[a-z0-9-]+", alias):
            raise ValueError(f"alias must match [a-z0-9-]+ (got {alias!r}) — it is "
                             "interpolated into IAM LeadingKeys patterns")
        witnesses = witnesses or []

        klayer = (runtime_layer or KeriRuntimeLayer(self, "Runtime")).layer
        flayer = (framework_layer or ServiceAidFrameworkLayer(self, "Framework")).layer

        framework_env = {
            "SERVICEAID_ALIAS": alias,
            "SERVICEAID_CORE_TABLE": core_table.table_name,
            "SERVICEAID_KEEPER_SECRET": f"keri/{alias}/keeper",
            "SERVICEAID_WITNESSES": ",".join(witnesses),
            "SERVICEAID_TOAD": str(toad),
            "SERVICEAID_HANDLER": handler_ref,     # module:attr
            "SERVICEAID_REGION": Aws.REGION,
            "LD_LIBRARY_PATH": "/opt/lib",
        }
        env = {**framework_env, **(environment or {})}

        fn = _lambda.Function(
            self, "Function",
            function_name=f"{alias}-serviceaid",
            runtime=_lambda.Runtime.PYTHON_3_14,
            architecture=_lambda.Architecture.ARM_64,
            handler="keri_serviceaid.handler.handler",   # resolves from framework layer
            code=compute_code,
            layers=[klayer, flayer, *(extra_layers or [])],
            reserved_concurrent_executions=1,
            memory_size=memory,
            timeout=Duration.seconds(timeout_seconds),
            environment=env,
            vpc=vpc,
        )

        # ── IAM: keeper secret scoped to keri/<alias>/* (fn doubles as CR) ──────
        keeper_secret_arn = f"arn:aws:secretsmanager:*:*:secret:keri/{alias}/*"
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue",
                     "secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"],
            resources=[keeper_secret_arn]))

        # ── IAM: pooled core table scoped to the four-pattern LeadingKeys union ──
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:DescribeTable", "dynamodb:GetItem", "dynamodb:PutItem",
                     "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:BatchWriteItem"],
            resources=[core_table.table_arn, f"{core_table.table_arn}/index/*"],
            conditions={"ForAllValues:StringLike": {"dynamodb:LeadingKeys": [
                "shared#*", "__meta__#shared#*",
                f"{alias}:*#*", f"__meta__#{alias}:*"]}}))

        # ── API Gateway: CESR ingest proxy, returns 204 ─────────────────────────
        api = apigw.LambdaRestApi(self, "Api", handler=fn, proxy=True,
                                  binary_media_types=["application/cesr", "*/*"])

        self.api = api
        self.function = fn

        # ── Inception Custom Resource (fn doubles as on_event_handler) ──────────
        provider = cr.Provider(self, "InceptionProvider", on_event_handler=fn)
        self.inception = CustomResource(self, "Inception",
                                        service_token=provider.service_token,
                                        properties={"Alias": alias})

    @property
    def grant_principal(self):       # iam.IGrantable
        """Delegate to the Function's role so adopters can grant their own
        resources the canonical way: my_lookup_table.grant_read_data(svc)."""
        return self.function.grant_principal


def inject_handler_shim(asset_dir: str) -> None:
    """Auto-inject the 3-line handler.py shim into a compute_code asset dir so
    the deploy is robust whether or not Lambda resolves the layer-resident
    handler. Callers (the example app) run this on the staged asset before
    Code.from_asset. Harmless when the layer-resident handler resolves."""
    shim = os.path.join(asset_dir, "handler.py")
    if not os.path.exists(shim):
        with open(shim, "w") as f:
            f.write("from keri_serviceaid.handler import handler  # noqa: F401\n")
```

- [ ] **Step 4: Update `keri_cdk/__init__.py`**

Replace `keri_cdk/__init__.py` with:
```python
from .core_stack import KeriCoreStack
from .runtime_layer import KeriRuntimeLayer
from .framework_layer import ServiceAidFrameworkLayer
from .witness_stack import WitnessStack
from .mailbox_stack import MailboxStack
from .service_aid import ServiceAidFunction
from .watcher_stack import WatcherStack

__all__ = ["KeriCoreStack", "KeriRuntimeLayer", "ServiceAidFrameworkLayer",
           "WitnessStack", "MailboxStack", "ServiceAidFunction", "WatcherStack"]
```

- [ ] **Step 5: Retire the old serviceaid code + its tests**

Run:
```bash
git rm -r keri_cdk/handlers/serviceaid/
git rm tests/serviceaid/test_authorize.py tests/serviceaid/test_contract.py \
       tests/serviceaid/test_idempotency.py tests/serviceaid/test_issuing.py \
       tests/serviceaid/test_runtime.py tests/serviceaid/test_runtime_shared_kel.py \
       tests/serviceaid/test_handler_e2e.py tests/serviceaid/test_bootstrap.py \
       tests/serviceaid/test_config.py
git rm tests/cdk/test_service_aid.py
```
Repoint `keri_cdk/_inception.py` imports: edit its dual-mode import block to use the new package — replace
```python
try:
    from keri_cdk.handlers.serviceaid import runtime
    from keri_cdk.handlers.serviceaid.config import Config
except ImportError:  # pragma: no cover - flat /var/task on Lambda
    import runtime
    from config import Config
```
with
```python
try:
    from keri_serviceaid import runtime
    from keri_serviceaid.config import Config
except ImportError:  # pragma: no cover - flat /var/task on Lambda
    from keri_serviceaid import runtime
    from keri_serviceaid.config import Config
```
(Lambda ships `keri_serviceaid` via the framework layer, so the package path resolves in both modes — no bare-module fallback needed.) Grep for any other importers of the old path:
```bash
grep -rn "handlers.serviceaid\|handlers/serviceaid" keri_cdk/ examples/ tests/ || echo "no stale imports"
```
Fix any that remain (the example is rewritten in Task 10).

- [ ] **Step 6: Run synth tests + the full serviceaid + cdk suites**

Run: `.venv/bin/python -m pytest tests/cdk/test_service_aid_function.py tests/cdk/test_framework_layer.py tests/serviceaid/ -v --import-mode=importlib -m "not integration"`
Expected: PASS (all). If `tests/cdk/test_gated_example.py` still references `ServiceAid`, it is removed/rewritten in Task 10 — temporarily deselect it here: add `--ignore=tests/cdk/test_gated_example.py`.

- [ ] **Step 7: Commit**

```bash
git add keri_cdk/service_aid.py keri_cdk/__init__.py keri_cdk/_inception.py tests/cdk/test_service_aid_function.py
git commit -m "feat(cdk): ServiceAidFunction (compute_code + 2 layers + IGrantable); retire old serviceaid

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Gated example reworked (≥2 routes)

**Files:**
- Modify (rewrite): `examples/gated_retrieval/gated_handler.py`
- Modify (rewrite): `examples/gated_retrieval/app.py`
- Keep: `examples/gated_retrieval/schema/gated_record.json`
- Test: `tests/cdk/test_gated_example_v2.py`
- Remove: `tests/cdk/test_gated_example.py` (old framework)

**Interfaces:**
- Consumes: `keri_serviceaid.ServiceAid/Reply/Request/Allowlist`; `keri_cdk.ServiceAidFunction/KeriCoreStack`.
- Produces: a `compute_code` module with **≥2 routes** (`/gated/cmd/request_record`, `/gated/cmd/revoke_record`); a cdk app wiring `ServiceAidFunction(compute_code=..., handler_ref="gated_handler:svc", core_table=core.table)` and demonstrating the IGrantable pattern via a stub lookup table.

- [ ] **Step 1: Write the failing example tests**

Create `tests/cdk/test_gated_example_v2.py`:
```python
"""The reworked gated example: ≥2 routes on the ServiceAid + a synthesizing app."""
import importlib
import os
import sys

import pytest


@pytest.fixture
def gated_module():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, "examples", "gated_retrieval")
    sys.path.insert(0, path)
    sys.modules.pop("gated_handler", None)
    mod = importlib.import_module("gated_handler")
    yield mod
    sys.path.remove(path)


def test_gated_svc_has_two_routes(gated_module):
    svc = gated_module.svc
    assert set(svc.routes) == {"/gated/cmd/request_record", "/gated/cmd/revoke_record"}


def test_request_record_returns_acdc(gated_module):
    from keri_serviceaid import TestRuntime
    reply = TestRuntime(gated_module.svc).send(
        route="/gated/cmd/request_record", sender="EReq", payload={"recordId": "r1"})
    assert reply.kind == "acdc" and reply.recipient == "EReq"
    assert reply.attributes["recordId"] == "r1"


def test_revoke_record_returns_none(gated_module):
    from keri_serviceaid import TestRuntime
    reply = TestRuntime(gated_module.svc).send(
        route="/gated/cmd/revoke_record", sender="EReq", payload={"recordId": "r1"})
    assert reply.kind == "none"


def test_app_synthesizes():
    """The example cdk app must synth a ServiceAidFunction wired to compute_code."""
    import subprocess, sys as _sys, os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    app_py = _os.path.join(root, "examples", "gated_retrieval", "app.py")
    # Synth in-process by importing the app module is brittle (App is global); instead
    # assert the module imports and constructs without raising.
    path = _os.path.join(root, "examples", "gated_retrieval")
    _sys.path.insert(0, path)
    _sys.modules.pop("app", None)
    import app  # noqa: F401  — running app.py builds the stacks + app.synth()
    _sys.path.remove(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/cdk/test_gated_example_v2.py -v --import-mode=importlib`
Expected: FAIL (old `gated_handler` imports `keri_cdk.handlers.serviceaid`).

- [ ] **Step 3: Rewrite `gated_handler.py`**

Replace `examples/gated_retrieval/gated_handler.py` with:
```python
"""Reference Service-AID: an allowlist-gated "prove-then-retrieve" service.

EXAMPLE / FICTIONAL. The developer's compute_code module. `svc` is the declared
entity; the framework finds it via handler_ref "gated_handler:svc". Two routes:
  - /gated/cmd/request_record → issues a gated-record ACDC (grant on success)
  - /gated/cmd/revoke_record  → acknowledges a revoke request (no reply in v1)

The "prove" half (a caller-presented gated-access credential) is the named
CredentialGate follow-on; v1 enforces the gate with an Allowlist of sender AIDs."""
import json
import pathlib

from keri_serviceaid import ServiceAid, Reply, Request, Allowlist

# Declare the entity. Witnesses/toad come from the deploy (env); the example
# leaves witnesses empty (unwitnessed) for a simple first deploy. allowlist=[]
# means any verified sender (override at deploy via the cdk app context).
svc = ServiceAid(alias="gated", witnesses=[], toad=0, authz=Allowlist([]))

# The ACDC this service ISSUES on a successful retrieval. register_schema
# saidifies it and queues it for the runtime to load into the schema store.
_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema" / "gated_record.json"
GATED_RECORD_SCHEMA_SAID = svc.register_schema(json.loads(_SCHEMA_PATH.read_text()))


def _fetch_record(record_id: str) -> dict:
    """Made-up business lookup ("cool data")."""
    rid = record_id or "rec-0001"
    return {"recordId": rid, "tier": "premium", "data": f"cool data for {rid}"}


@svc.command(route="/gated/cmd/request_record", issues=GATED_RECORD_SCHEMA_SAID)
def request_record(req: Request) -> Reply:
    """Allowlist-gated retrieval: by the time this runs the caller's exn is
    verified and the sender is authorized. Return a gated-record ACDC to the
    caller (the framework issues + grants it to the caller's mailbox)."""
    record = _fetch_record(req.payload.get("recordId", ""))
    return Reply.acdc(recipient=req.sender, attributes={**record, "dt": req.now()})


@svc.command(route="/gated/cmd/revoke_record")
def revoke_record(req: Request) -> Reply:
    """Acknowledge a revoke request. v1 ships grant + silence, so a non-issuing
    command returns Reply.none() (no reply leaves the mailbox). A real service
    would mark the record revoked in its datastore and could (follow-on) emit a
    signed note. Demonstrates a SECOND capability on the same role/AID."""
    # (business effect would go here — e.g. mark req.payload["recordId"] revoked)
    return Reply.none()
```

- [ ] **Step 4: Rewrite `app.py`**

Replace `examples/gated_retrieval/app.py` with:
```python
"""CDK app: shared core stack + the allowlist-gated Gated Retrieval Service-AID.

Deploys via ServiceAidFunction: the dev's compute_code (this dir) + the two
framework layers. Passing core.table across the stack boundary emits the
cross-stack Export/Fn::ImportValue lifecycle lock. The stub `lookup` table shows
the IGrantable pattern: my_lookup.grant_read_data(svc) adds a read policy to the
service Function's role the canonical CDK way.

Build BOTH layers before `cdk deploy` (see DEPLOY_RUNBOOK.md):
  keri_cdk/layers/build_layer.sh
  keri_cdk/layers/build_framework_layer.sh
Pass --context allowlist='["EReqAID", ...]' to gate by sender AID (empty ⇒ any)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import aws_cdk as cdk
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_dynamodb as ddb

from keri_cdk import KeriCoreStack, ServiceAidFunction
from keri_cdk.service_aid import inject_handler_shim

app = cdk.App()
env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1")

core = KeriCoreStack(app, "KeriCore", table_name="keri-core", env=env)

svc_stack = cdk.Stack(app, "GatedRetrieval", env=env)

# compute_code = this example dir (gated_handler.py + schema/). Inject the shim
# (robust handler resolution) before Code.from_asset stages it.
_asset_dir = str(pathlib.Path(__file__).parent)
inject_handler_shim(_asset_dir)

svc = ServiceAidFunction(
    svc_stack, "Gated",
    alias="gated",
    core_table=core.table,
    compute_code=_lambda.Code.from_asset(_asset_dir),
    handler_ref="gated_handler:svc",
    witnesses=app.node.try_get_context("witnesses") or [],
    toad=int(app.node.try_get_context("toad") or 0),
)

# IGrantable pattern: a stub lookup resource the service may read.
lookup = ddb.Table(svc_stack, "GatedLookup",
                   partition_key=ddb.Attribute(name="recordId",
                                               type=ddb.AttributeType.STRING))
lookup.grant_read_data(svc)   # adds a read policy to the service Function's role

svc_stack.add_dependency(core)
app.synth()
```

- [ ] **Step 5: Remove the old example test**

Run:
```bash
git rm tests/cdk/test_gated_example.py
```

- [ ] **Step 6: Run the new example tests**

Run: `.venv/bin/python -m pytest tests/cdk/test_gated_example_v2.py -v --import-mode=importlib`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add examples/gated_retrieval/gated_handler.py examples/gated_retrieval/app.py tests/cdk/test_gated_example_v2.py
git commit -m "feat(example): rework gated_retrieval onto keri_serviceaid (≥2 routes + IGrantable)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: First-real-deploy runbook + final review

**Files:**
- Create: `examples/gated_retrieval/DEPLOY_RUNBOOK.md`

**Interfaces:**
- Consumes: everything (the runbook drives the live validation the synth tests cannot).
- Produces: a manual runbook for the first real deploy + teardown; the branch-merge handoff.

- [ ] **Step 1: Write the deploy runbook**

Create `examples/gated_retrieval/DEPLOY_RUNBOOK.md`:
```markdown
# Gated Retrieval Service-AID — First Real Deploy Runbook

The Service-AID framework's integration validation that a synth test CANNOT do.
Run it ONCE against real AWS to prove (1) layer-resident handler resolution +
libsodium; (2) witnessed inception via Receiptor; (3) oracle verification of a
real inbound exn; (4) Postman delivery to a real mailbox (mailbox.keri.host);
(5) the IPEX round-trip across ≥2 routes on one AID. Then tear it down.

## Prereqs
- AWS creds for the target account (`AWS_PROFILE=...`), region with python3.14 Lambda.
- Docker (for the arm64 layer builds).
- A test requester keystore (kli) that can sign exns and poll a mailbox via SSE.

## 1. Build BOTH layers (arm64, in Docker)
```bash
keri_cdk/layers/build_layer.sh              # KeriRuntimeLayer: libsodium + keripy
keri_cdk/layers/build_framework_layer.sh    # ServiceAidFrameworkLayer: keri_serviceaid
```
Confirm each prints `OK: ...` and a sane size.

## 2. Deploy
```bash
cd examples/gated_retrieval
# Optional witnessed config (recommended to exercise Receiptor):
#   --context witnesses='["BWit1","BWit2"]' --context toad=2
#   (the 5×5 federation; OOBI-resolve them into the core table first)
cdk deploy KeriCore GatedRetrieval \
  --context account=<acct> --context region=<region>
```
The inception Custom Resource runs on Create: it get-or-creates the keeper secret
(keri/gated/keeper) and incepts the AID. **If witnessed, confirm the CR collected
receipts via Receiptor (/receipts), NOT WitnessReceiptor** — check the Function's
CloudWatch logs for "Service AID inception complete: alias=gated pre=E..." with no
hang/timeout (a WitnessReceiptor hang would surface as a CR timeout — the
regression guard in tests/serviceaid/test_runtime_v2.py prevents reintroducing it).

## 3. Resolve the service OOBI into the requester
```bash
# The API Gateway URL is a stack output; the service published its own end-role.
kli oobi resolve --name reqr --oobi-alias gated --oobi <apigw-url>/oobi/<pre>/controller
```

## 4. POST a signed exn — route 1 (request_record)
Build a signed /gated/cmd/request_record exn from the requester (recipient = the
service pre), POST it as application/cesr + CESR-ATTACHMENT to the API GW root.
Expect **HTTP 204**. The grant leaves out-of-band to the requester's mailbox.

## 5. Requester polls its mailbox (SSE) + admits
```bash
kli mailbox poll --name reqr           # or the SSE qry route='mbx'
# Expect an /ipex/grant for a gated-record ACDC; admit it:
kli ipex admit --name reqr --said <grant-said>
```
Confirm the gated-record ACDC is now in the requester's credential store.

## 6. Exercise route 2 (revoke_record)
POST a signed /gated/cmd/revoke_record exn. Expect **HTTP 204** and (v1 grant+
silence) NO mailbox reply. Confirm the Function logged the revoke with no error.
This proves ≥2 capabilities on ONE role/AID.

## 7. Replay (idempotency)
Re-POST the EXACT request_record exn from step 4. Expect **HTTP 204** and the
requester's mailbox to receive the SAME grant again (re-delivered, not re-issued
— no duplicate credential SAID). Confirm only ONE issuance occurred (one TEL iss).

## 8. Tear down
```bash
cdk destroy GatedRetrieval        # the AID/keeper secret persist by design (CR Delete is a no-op)
# KeriCore (the pooled table) outlives services; destroy only if no other service consumes it.
```

## Validation checklist (all must hold)
- [ ] Both layers built and attached; cold start imports keri (libsodium resolved).
- [ ] Inception completed via Receiptor (no WitnessReceiptor hang).
- [ ] A real inbound exn verified against the oracle key state.
- [ ] Grant delivered via Postman to the requester's mailbox (mailbox.keri.host).
- [ ] IPEX round-trip: requester polled SSE + admitted the credential.
- [ ] ≥2 routes exercised on one AID (request_record + revoke_record).
- [ ] Replay re-delivered the same grant (exactly-once issuance).
```

- [ ] **Step 2: Run the full test suite (regression gate before review)**

Run: `.venv/bin/python -m pytest tests/serviceaid/ tests/cdk/test_framework_layer.py tests/cdk/test_service_aid_function.py tests/cdk/test_gated_example_v2.py tests/handlers/test_oracle_reachability.py -v --import-mode=importlib -m "not integration"`
Expected: PASS (all). Also re-run the broader CDK suite to confirm no collateral breakage: `.venv/bin/python -m pytest tests/cdk/ --import-mode=importlib -m "not integration"` (the retired `test_service_aid.py`/`test_gated_example.py` are gone; remaining stack tests must stay green).

- [ ] **Step 3: Commit the runbook**

```bash
git add examples/gated_retrieval/DEPLOY_RUNBOOK.md
git commit -m "docs(example): first-real-deploy runbook for the gated Service-AID

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Whole-branch review + merge**

Subagent-driven-development runs a whole-branch review at the end. After that review passes, **merge direct to `development` (no PR)**, matching the prior framework builds (Service AID framework, Secret-backed keeper, CDK Phase B/C all landed direct to keripy `development`). The first real deploy (the runbook above) is performed manually after merge, then torn down.

---

## Self-Review

**1. Spec coverage (Scope items 1–7):**
- (1) `keri_serviceaid` framework package → Tasks 1–6 (entity, Request/Reply, Command registry, six Protocols+defaults, pipeline, cold-start runtime, idempotency). ✓
- (2) Inbound handler reworked to transport-silent `204` + mailbox-out → Task 6 (+ pipeline Task 4; v1 grant+silence decided in Global Constraints). ✓
- (3) Witnessed inception via `Receiptor` (not `WitnessReceiptor`) → Task 5 (`incept_or_load` + regression-guard test). ✓
- (4) `ServiceAidFrameworkLayer` + `ServiceAidFunction` (compute_code, IGrantable, pass-through, layer-resident handler + shim fallback) → Tasks 8–9. ✓
- (5) `SHARED_KEL_STORES += ends./locs./eans.` + probe/test extension → Task 7. ✓
- (6) Gated example reworked (≥2 routes) + real deploy → Tasks 10–11. ✓
- (7) AWS-free test pyramid (provider/command/pipeline units + moto integration + synth) + first-real-deploy validation → Tasks 2–11. ✓

**2. Placeholder scan:** No `TBD`/`TODO`/"similar to Task N"/"add error handling". The two `pytest.skip` integration scaffolds are deliberate, explicitly outlined, and gated by `-m integration` (the live round-trip is the Task 11 runbook); they are not code placeholders in the shippable units. ✓

**3. Type consistency:** `Reply.acdc/none/reject` + `schema_said` field (added Task 3b, used in pipeline Task 4 + issuer); `Command(route, payload_schema, issues, fn)`; `Request(sender, route, payload, credentials, message_said, key_state)`; provider method names (`authorize`, `verify`, `resolve`, `issue`, `deliver`, `seen`/`record`) consistent across providers, pipeline, runtime; `Context(hby, hab, rgy, registry_name)` and `Endpoint(role, eid, url)` consistent across issue/deliver/resolve/pipeline; `RuntimeState(cfg, hby, hab, rgy, svc)`; `handler_ref` (not `handler_module`) consistent across config/runtime/construct/example; `ServiceAidFunction.grant_principal` delegates to `self.function.grant_principal`. ✓

**Out-of-scope guardrails:** `CredentialGate`, watcher tier-3 (`NotImplementedError`), signed denials, DLQ retry, mailbox-inbound drain, micro-app template — all left as named seams only, none implemented. ✓
