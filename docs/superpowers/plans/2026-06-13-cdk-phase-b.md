# keri_cdk Library + keri-host Ecosystem (Phase B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the SAM-based serverless KERI stacks into a distributable Python CDK construct library (`keri_cdk`) plus a `keri_host` ecosystem app (witness + mailbox) and a `gated_retrieval` example app, with zip+layer runtime, the core-table cross-stack lock, reserved-concurrency, and false-404 responder retries.

**Architecture:** A `keri_cdk` package holds the reusable stacks/constructs (`KeriCoreStack`, `KeriRuntimeLayer`, `WitnessStack`, `MailboxStack`, `ServiceAid`, `WatcherStack` seam) + the generic infra handlers (witness/mailbox/serviceaid). Per-ecosystem CDK apps compose them. Lambdas are pure-Python zips riding a prebuilt arm64 `KeriRuntimeLayer` (libsodium + keripy native deps); the mailbox additionally rides the AWS LWA layer for SSE streaming.

**Tech Stack:** Python, aws-cdk-lib + constructs, DynamoDB, Lambda (zip + layers), API Gateway REST, ACM, Route53, Secrets Manager, moto + `aws_cdk.assertions` (tests), boto3 (real-AWS validation). keripy fork.

**Spec:** `docs/superpowers/specs/2026-06-13-cdk-phase-b-design.md`

**Source-of-truth note:** the existing `sam-witness/template.yaml` and `sam-mailbox/template.yaml` encode the exact per-service resources (DynamoDB schema, env vars, IAM, API GW routes, ACM cert, API GW custom domain, Route53 A-record, LWA/streaming config). Each CDK stack task below = **translate that template's resources into CDK L2 constructs, preserving the listed properties**, with the deltas this plan specifies (zip+layer, reserved-concurrency, lock, retries).

---

## File Structure

```
keri_cdk/
├── __init__.py                  # exports KeriCoreStack, KeriRuntimeLayer, WitnessStack,
│                                #          MailboxStack, ServiceAid, WatcherStack
├── core_stack.py                # KeriCoreStack            (from cdk/keri_core_stack.py)
├── runtime_layer.py             # KeriRuntimeLayer construct
├── witness_stack.py             # WitnessStack             (new)
├── mailbox_stack.py             # MailboxStack             (new)
├── service_aid.py               # ServiceAid + inception   (from cdk/service_aid_construct.py + inception.py)
├── watcher_stack.py             # WatcherStack             (seam only)
├── handlers/
│   ├── witness/                 # from sam-witness/ (witness_handler.py + bootstrap.py)
│   ├── mailbox/                 # from sam-mailbox/ (mailbox_handler.py + bootstrap.py + run.sh)
│   └── serviceaid/              # from service-aid/serviceaid/ (config/contract/issuing/
│                                #   authorize/idempotency/runtime/handler)
└── layers/
    ├── build_layer.sh           # builds the arm64 KeriRuntimeLayer asset (AL container)
    └── keri_runtime/            # the BUILT layer asset (python/ + lib/) — produced by build_layer.sh
ecosystems/keri_host/{app.py,cdk.json}
examples/gated_retrieval/{app.py,gated_handler.py,schema/,cdk.json}
tests/cdk/                       # CDK assertion tests (test_core_stack.py, test_witness_stack.py, ...)
keri_cdk/probes/                 # real-AWS validation scripts (layer smoke, deploy validation)
```
Removed at the end (Task 10 / cleanup): `sam-witness/`, `sam-mailbox/`, `service-aid/`.

---

## Task 0: Worktree venv + clean baseline

**Files:** none (environment)

- [ ] **Step 1: Create venv + install deps**

Run from `~/code/keripy/.worktrees/cdk-phaseB`:
```bash
python3 -m venv .venv
.venv/bin/pip install -q -e . "aws-cdk-lib>=2.140.0" constructs moto boto3 pytest pytest-asyncio falcon uvicorn
```

- [ ] **Step 2: Baseline — existing service-aid + dynamodbing suites green**

Run: `.venv/bin/python -m pytest service-aid/tests/ tests/db/test_dynamodbing.py -q`
Expected: pass (the pre-conversion baseline). If red, STOP and report.

- [ ] **Step 3: Confirm CDK CLI available** (for later real-AWS tasks)

Run: `npx cdk --version` (or `cdk --version`). Expected: a version string. If absent, note that Tasks 3/9 need the CDK CLI installed (`npm i -g aws-cdk`).

---

## Task 1: Scaffold `keri_cdk` + move `KeriCoreStack` (PITR, deletion/termination protection, lock-ready)

**Files:**
- Create: `keri_cdk/__init__.py`, `keri_cdk/core_stack.py`
- Test: `tests/cdk/test_core_stack.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cdk/test_core_stack.py`:
```python
import aws_cdk as cdk
from aws_cdk.assertions import Template
from keri_cdk import KeriCoreStack

def _synth():
    app = cdk.App()
    stack = KeriCoreStack(app, "Core", table_name="keri-core")
    return Template.from_stack(stack), stack

def test_core_table_pitr_and_deletion_protection():
    t, _ = _synth()
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "TableName": "keri-core",
        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        "DeletionProtectionEnabled": True,
    })
    # subdb-index GSI present
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "GlobalSecondaryIndexes": [{"IndexName": "subdb-index"}],
    })

def test_core_table_retained_and_stack_termination_protected():
    t, stack = _synth()
    t.has_resource("AWS::DynamoDB::Table", {"DeletionPolicy": "Retain"})
    assert stack.termination_protection is True

def test_core_table_exposed_as_attribute():
    _, stack = _synth()
    assert stack.table is not None
```

- [ ] **Step 2: Run → fail**

Run: `.venv/bin/python -m pytest tests/cdk/test_core_stack.py -q`
Expected: FAIL (`keri_cdk` doesn't exist yet / properties missing).

- [ ] **Step 3: Create the package + move `KeriCoreStack`**
```bash
mkdir -p keri_cdk
git mv service-aid/serviceaid/cdk/keri_core_stack.py keri_cdk/core_stack.py
```
Create `keri_cdk/__init__.py`:
```python
from .core_stack import KeriCoreStack
# (later tasks append: KeriRuntimeLayer, WitnessStack, MailboxStack, ServiceAid, WatcherStack)
__all__ = ["KeriCoreStack"]
```

- [ ] **Step 4: Harden `KeriCoreStack`** (`keri_cdk/core_stack.py`)

Set termination protection on the stack and PITR + deletion protection on the table. In `__init__`, pass `termination_protection=True` through to `super().__init__` (merge into `**kw` default), and on the `ddb.Table(...)` add:
```python
        point_in_time_recovery=True,
        deletion_protection=True,
```
(Keep `removal_policy=RemovalPolicy.RETAIN`, the fixed `table_name`, the `subdb-index` GSI, the SSM param + `CfnOutput`, and `self.table`.) For termination protection, set it in the constructor:
```python
    def __init__(self, scope, cid, *, table_name="keri-core", **kw):
        kw.setdefault("termination_protection", True)
        super().__init__(scope, cid, **kw)
```

- [ ] **Step 5: Run → pass**

Run: `.venv/bin/python -m pytest tests/cdk/test_core_stack.py -q` → PASS.

- [ ] **Step 6: Commit**
```bash
git add keri_cdk/ tests/cdk/test_core_stack.py
git commit -m "feat(keri_cdk): scaffold library; move + harden KeriCoreStack (PITR, deletion/termination protection)"
```

---

## Task 2: Relocate handler code into `keri_cdk/handlers/` + false-404 responder retries

**Files:**
- Move: `sam-witness/witness_handler.py` + `bootstrap.py` → `keri_cdk/handlers/witness/`; `sam-mailbox/mailbox_handler.py` + `bootstrap.py` → `keri_cdk/handlers/mailbox/`; `service-aid/serviceaid/{config,contract,issuing,authorize,idempotency,runtime,handler}.py` → `keri_cdk/handlers/serviceaid/`
- Create: `keri_cdk/handlers/{witness,mailbox,serviceaid}/__init__.py`
- Test: `tests/cdk/test_responder_retry.py` (+ relocate the moved unit tests)

- [ ] **Step 1: Move the handler code (preserve logic)**
```bash
mkdir -p keri_cdk/handlers/witness keri_cdk/handlers/mailbox keri_cdk/handlers/serviceaid
git mv sam-witness/witness_handler.py keri_cdk/handlers/witness/witness_handler.py
git mv sam-witness/bootstrap.py keri_cdk/handlers/witness/bootstrap.py
git mv sam-mailbox/mailbox_handler.py keri_cdk/handlers/mailbox/mailbox_handler.py
git mv sam-mailbox/bootstrap.py keri_cdk/handlers/mailbox/bootstrap.py
for m in config contract issuing authorize idempotency runtime handler; do
  git mv service-aid/serviceaid/$m.py keri_cdk/handlers/serviceaid/$m.py
done
touch keri_cdk/handlers/__init__.py keri_cdk/handlers/witness/__init__.py \
      keri_cdk/handlers/mailbox/__init__.py keri_cdk/handlers/serviceaid/__init__.py
```
Fix imports in the moved `serviceaid` modules: anything importing `serviceaid.X` becomes `keri_cdk.handlers.serviceaid.X` (or relative `from . import X`). Grep: `grep -rn "from serviceaid\|import serviceaid" keri_cdk/handlers/`. The witness/mailbox handlers import only `keri.*` (unchanged).

- [ ] **Step 2: Relocate the moved unit tests + confirm green**

Move the serviceaid unit tests so they still run against the new import paths:
```bash
mkdir -p tests/serviceaid
git mv service-aid/tests/test_authorize.py tests/serviceaid/ ; # ...repeat for config/contract/issuing/idempotency/runtime/handler_e2e/_schema/conftest
```
Update their imports (`serviceaid.X` → `keri_cdk.handlers.serviceaid.X`). Run `.venv/bin/python -m pytest tests/serviceaid/ -q` → PASS (logic unchanged). (`test_cdk_synth.py`/`test_inception.py`/`test_example_rating.py`/`test_integration_local.py` are superseded by later tasks — delete them; they reference the old construct/example.)

- [ ] **Step 3: Write the failing responder-retry test**

Create `tests/cdk/test_responder_retry.py`. The witness handler exposes a helper for the negative-result retry; test it directly. First add a small helper to `keri_cdk/handlers/witness/witness_handler.py`:
```python
def _retry_negative(read, *, attempts=4, delay=0.05):
    """Retry a GSI-served read that returns falsy (eventual-consistency lag) up to
    `attempts` times. A truthy result is returned immediately; only the not-found
    path retries. Returns the last (possibly falsy) result."""
    import time
    result = read()
    for _ in range(attempts - 1):
        if result:
            return result
        time.sleep(delay)
        result = read()
    return result
```
Test:
```python
from keri_cdk.handlers.witness import witness_handler as wh

def test_retry_negative_returns_first_truthy_without_extra_calls():
    calls = {"n": 0}
    def read():
        calls["n"] += 1
        return "hit"
    assert wh._retry_negative(read, attempts=4, delay=0) == "hit"
    assert calls["n"] == 1            # positive trusted immediately, no retry

def test_retry_negative_retries_until_value_appears():
    seq = [None, None, "late"]
    it = iter(seq)
    assert wh._retry_negative(lambda: next(it), attempts=4, delay=0) == "late"

def test_retry_negative_gives_up_after_attempts():
    assert wh._retry_negative(lambda: None, attempts=3, delay=0) is None
```

- [ ] **Step 4: Run → fail** (`_retry_negative` not yet wired into the responders).
Run: `.venv/bin/python -m pytest tests/cdk/test_responder_retry.py -q` → the helper tests pass once the helper exists; then wire it.

- [ ] **Step 5: Wire `_retry_negative` into the negative-result responders**

In `keri_cdk/handlers/witness/witness_handler.py`, wrap the GSI-served negative paths:
- `handle_receipt_get`: wrap the `kels.getLast(...)` lookup and the `wigs.get(...)` lookup so a transient empty result retries before returning 404.
- `handle_oobi_get`: wrap the `_hby.db.fullyWitnessed(kever.serder)` check (retry while False before returning the 404/"not fully witnessed").
Apply the same to the mailbox/query `fullyWitnessed` gates where they currently return not-found. Keep positives immediate. (These are localized wraps around existing reads — preserve all other logic.)

- [ ] **Step 6: Run → pass + full moved-suite green**

Run: `.venv/bin/python -m pytest tests/cdk/test_responder_retry.py tests/serviceaid/ -q` → PASS.

- [ ] **Step 7: Commit**
```bash
git add keri_cdk/ tests/
git commit -m "refactor(keri_cdk): relocate witness/mailbox/serviceaid handlers into library; add false-404 responder retry"
```

---

## Task 3: `KeriRuntimeLayer` construct + real-AWS zip+layer witness smoke

**Files:**
- Create: `keri_cdk/runtime_layer.py`, `keri_cdk/layers/build_layer.sh`, `keri_cdk/probes/layer_smoke/{probe.py,README.md}`
- Test: `tests/cdk/test_runtime_layer.py`

- [ ] **Step 1: Write the failing test**

`tests/cdk/test_runtime_layer.py`:
```python
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import KeriRuntimeLayer

def test_layer_is_arm64_python():
    app = cdk.App()
    stack = cdk.Stack(app, "S")
    KeriRuntimeLayer(stack, "Layer")
    t = Template.from_stack(stack)
    t.has_resource_properties("AWS::Lambda::LayerVersion", {
        "CompatibleArchitectures": ["arm64"],
        "CompatibleRuntimes": Match.array_with(["python3.13"]),
    })
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Write the build script** `keri_cdk/layers/build_layer.sh`
```bash
#!/usr/bin/env bash
# Build the arm64 KeriRuntimeLayer asset: keripy + native deps as manylinux arm64 wheels,
# laid out for a Lambda layer (python/ for packages, lib/ for libsodium .so).
set -euo pipefail
OUT="$(dirname "$0")/keri_runtime"
rm -rf "$OUT" && mkdir -p "$OUT/python" "$OUT/lib"
docker run --rm --platform linux/arm64 -v "$PWD":/work -w /work \
  public.ecr.aws/lambda/python:3.13-arm64 \
  /bin/sh -c "pip install -e . -t '$OUT/python' && \
              cp \$(python -c 'import pysodium,os;print(os.path.dirname(pysodium.__file__))')/../*.so '$OUT/lib' 2>/dev/null || true"
echo "layer built at $OUT (python/ + lib/)"
```
(The exact libsodium `.so` copy may need adjusting to where the AL image places it — the implementer verifies the smoke in Step 7 proves it resolves. Reuse `sam-witness/lib/` contents as a reference for what the container shipped.)

- [ ] **Step 4: Write `keri_cdk/runtime_layer.py`**
```python
import os
from aws_cdk import aws_lambda as _lambda
from constructs import Construct

_ASSET = os.path.join(os.path.dirname(__file__), "layers", "keri_runtime")

class KeriRuntimeLayer(Construct):
    """Prebuilt arm64 layer carrying libsodium + keripy's native deps, so consumers
    deploy pure-Python zip functions with no Docker. Build the asset with
    keri_cdk/layers/build_layer.sh (CI / release time)."""
    def __init__(self, scope: Construct, cid: str, *, asset_path: str = _ASSET, **kw):
        super().__init__(scope, cid, **kw)
        self.layer = _lambda.LayerVersion(
            self, "KeriRuntime",
            code=_lambda.Code.from_asset(asset_path),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13],
            compatible_architectures=[_lambda.Architecture.ARM_64],
            description="libsodium + keripy native deps (arm64)",
        )
```
Export it from `keri_cdk/__init__.py` (`from .runtime_layer import KeriRuntimeLayer`; add to `__all__`). For the synth test to run without a real build, the test points at a throwaway dir or the build is run first; the implementer ensures `layers/keri_runtime/` exists (run `build_layer.sh` if Docker is available, else create a placeholder `python/` dir for the synth-only test and build for real in Step 6).

- [ ] **Step 5: Run → pass** (`tests/cdk/test_runtime_layer.py`).

- [ ] **Step 6: Build the real layer**

Run: `bash keri_cdk/layers/build_layer.sh` (needs Docker). Confirm `keri_cdk/layers/keri_runtime/python/keri/` and a libsodium `.so` under `lib/` exist, and the unzipped size is < 250 MB (`du -sh keri_cdk/layers/keri_runtime`).

- [ ] **Step 7: Real-AWS zip+layer witness smoke** `keri_cdk/probes/layer_smoke/probe.py`

A boto3 script (mirror `service-aid/probes/*/probe.py` conventions: `sts.get_caller_identity` preflight expecting account `117870855864`, throwaway resource names, teardown + leftover scan, `--keep`/`--teardown-only`). It: creates a Baser DynamoDB table + the keeper secret; publishes the `KeriRuntimeLayer` content as a real Lambda layer; deploys a zip witness Lambda (`keri_cdk/handlers/witness`) with that layer, `LD_LIBRARY_PATH=/opt/lib`, arm64; invokes it (GET `/` status + a self-OOBI) and asserts it **incepts, signs, and returns an OOBI** — proving libsodium resolves from `/opt/lib`. Tear down; verify zero leftovers.
Run: `AWS_PROFILE=personal .venv/bin/python keri_cdk/probes/layer_smoke/probe.py --region us-east-1` → PASS (witness AID returned; OOBI served). This is the load-bearing proof of the runtime model.

- [ ] **Step 8: Commit**
```bash
git add keri_cdk/runtime_layer.py keri_cdk/layers/build_layer.sh keri_cdk/probes/layer_smoke/ keri_cdk/__init__.py tests/cdk/test_runtime_layer.py
git commit -m "feat(keri_cdk): KeriRuntimeLayer (prebuilt arm64) + real-AWS zip+layer witness smoke"
```

---

## Task 4: `WitnessStack` (zip+layer, reserved-concurrency=1, domain from props)

**Files:**
- Create: `keri_cdk/witness_stack.py`
- Test: `tests/cdk/test_witness_stack.py`
- Reference (translate from): `sam-witness/template.yaml`

- [ ] **Step 1: Write the failing test** `tests/cdk/test_witness_stack.py`
```python
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import WitnessStack

def _synth():
    app = cdk.App()
    s = WitnessStack(app, "Wit", name="witness-test", alias="witness",
                     domain_name="witness.example.com", hosted_zone_id="Z123",
                     witness_url="https://witness.example.com")
    return Template.from_stack(s)

def test_witness_zip_arm64_reserved_concurrency_layer():
    t = _synth()
    t.has_resource_properties("AWS::Lambda::Function", {
        "Architectures": ["arm64"],
        "ReservedConcurrentExecutions": 1,
        "Handler": "witness_handler.handler",
        "Runtime": "python3.13",
        "Layers": Match.any_value(),
        "Environment": {"Variables": Match.object_like({"LD_LIBRARY_PATH": "/opt/lib"})},
    })

def test_witness_owns_its_baser_table_with_gsi():
    t = _synth()
    t.resource_count_is("AWS::DynamoDB::Table", 1)
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "GlobalSecondaryIndexes": [{"IndexName": "subdb-index"}]})

def test_witness_scoped_secrets_manager_iam():
    t = _synth()
    t.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {"Statement": Match.array_with([Match.object_like({
            "Action": Match.array_with(["secretsmanager:GetSecretValue"]),
        })])}})
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement `keri_cdk/witness_stack.py`**

Translate `sam-witness/template.yaml` to CDK, preserving: the Baser DynamoDB table (`{name}-db`, PK/SK + `subdb-index` GSI), all `WITNESS_*` env vars (`WITNESS_NAME`/`WITNESS_ALIAS`/`WITNESS_BASER_TABLE`/`WITNESS_KEEPER_SECRET=keri/{stack}/keeper`/`WITNESS_REGION`/`WITNESS_URL`/`LD_LIBRARY_PATH=/opt/lib`), the scoped Secrets Manager IAM (`Get/Create/Put` on `keri/{stack}/*`), the Baser table grant, the API GW routes (POST/PUT/GET `/`, GET `/receipts`, GET `/query`, GET `/oobi/*`), ACM cert + API GW custom domain + Route53 A-record from `domain_name`/`hosted_zone_id`. Deltas: **zip + layer** (`code=Code.from_asset("keri_cdk/handlers/witness")`, `handler="witness_handler.handler"`, `runtime=PYTHON_3_13`, `architecture=ARM_64`, `layers=[runtime_layer or KeriRuntimeLayer(self,"Layer").layer]`), **`reserved_concurrent_executions=1`**. Constructor props: `name, alias, domain_name, hosted_zone_id, witness_url, keeper_secret=None, witnesses=None, toad=0, runtime_layer=None`. Export from `__init__.py`.

- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `git commit -am "feat(keri_cdk): WitnessStack (zip+layer, reserved-concurrency=1)"`

---

## Task 5: `MailboxStack` (zip+layer + LWA + streaming)

**Files:**
- Create: `keri_cdk/mailbox_stack.py`, `keri_cdk/handlers/mailbox/run.sh`
- Test: `tests/cdk/test_mailbox_stack.py`
- Reference: `sam-mailbox/template.yaml`

- [ ] **Step 1: Write the failing test** `tests/cdk/test_mailbox_stack.py`
```python
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import MailboxStack

def _synth():
    app = cdk.App()
    s = MailboxStack(app, "Mbx", name="mailbox-test", alias="mailbox",
                     domain_name="mailbox.example.com", hosted_zone_id="Z123",
                     mailbox_url="https://mailbox.example.com")
    return Template.from_stack(s)

def test_mailbox_lwa_streaming_env_and_no_reserved_concurrency():
    t = _synth()
    fns = t.find_resources("AWS::Lambda::Function")
    props = list(fns.values())[0]["Properties"]
    env = props["Environment"]["Variables"]
    assert env["AWS_LWA_INVOKE_MODE"] == "response_stream"
    assert env["LD_LIBRARY_PATH"] == "/opt/lib"
    assert "ReservedConcurrentExecutions" not in props   # NOT capped
    assert props["Architectures"] == ["arm64"]
    assert len(props["Layers"]) >= 2                      # KeriRuntimeLayer + LWA

def test_mailbox_api_is_regional():
    t = _synth()
    t.has_resource_properties("AWS::ApiGateway::RestApi", {
        "EndpointConfiguration": {"Types": ["REGIONAL"]}})
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Add `run.sh`** `keri_cdk/handlers/mailbox/run.sh`
```bash
#!/bin/bash
exec python -m uvicorn mailbox_handler:app --host 0.0.0.0 --port "${AWS_LWA_PORT:-8080}"
```
(`chmod +x`. Confirm the mailbox handler exposes the ASGI `app` via `build_app()`; if the module-level symbol differs, match it.)

- [ ] **Step 4: Implement `keri_cdk/mailbox_stack.py`**

Translate `sam-mailbox/template.yaml`, preserving: the Baser table (`{name}-db`, shared baser+mailboxer stores, PK/SK + GSI), the `MAILBOX_*` env + the LWA env (`AWS_LWA_INVOKE_MODE=response_stream`, `AWS_LWA_PORT`, `AWS_LWA_READINESS_CHECK_PATH=/status`, `LD_LIBRARY_PATH=/opt/lib`), the scoped Secrets Manager IAM, the Baser grant, the explicit API GW REST **REGIONAL** endpoint, ACM cert + custom domain + Route53. Deltas: **zip + TWO layers** (`KeriRuntimeLayer` + the AWS LWA arm64 layer via `LayerVersion.from_layer_version_arn`, pinned), `handler` per the LWA zip pattern (the `run.sh` exec-wrapper: set `AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap` and the function handler to `run.sh`, OR the extension style — verify against the pinned LWA layer), `timeout=Duration.minutes(15)`, NO reserved concurrency. **Streaming integration:** use `apigw.LambdaIntegration(fn, response_transfer_mode=apigw.ResponseTransferMode.STREAM, timeout=Duration.minutes(15))` if the pinned `aws-cdk-lib` exposes `ResponseTransferMode`; ELSE fall back to a `CfnMethod` escape hatch (`responseTransferMode: "STREAM"` + the `.../response-streaming-invocations` integration URI). Constructor props mirror WitnessStack + `mailbox_url`, `witness_aid=""`, `witness_url=""`. Export from `__init__.py`.

- [ ] **Step 5: Run → pass.**
- [ ] **Step 6: Commit** `git commit -am "feat(keri_cdk): MailboxStack (zip+layer+LWA, regional streaming)"`

---

## Task 6: `ServiceAid` cross-stack lock + Gated Retrieval example

**Files:**
- Modify/move: `service-aid/serviceaid/cdk/service_aid_construct.py` + `inception.py` → `keri_cdk/service_aid.py`
- Create: `examples/gated_retrieval/{app.py,gated_handler.py,schema/,cdk.json}`
- Test: `tests/cdk/test_service_aid.py`, `tests/cdk/test_gated_example.py`

- [ ] **Step 1: Write the failing test** `tests/cdk/test_service_aid.py`
```python
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import KeriCoreStack, ServiceAid

def _synth():
    app = cdk.App()
    core = KeriCoreStack(app, "Core", table_name="keri-core")
    svc = cdk.Stack(app, "Svc")
    ServiceAid(svc, "Gated", alias="gated", core_table=core.table,
               handler_module="gated_handler", allowlist=["EReq..."])
    return Template.from_stack(svc), Template.from_stack(core)

def test_service_aid_zip_layer_reserved_concurrency():
    svc, _ = _synth()
    svc.has_resource_properties("AWS::Lambda::Function", {
        "Architectures": ["arm64"], "ReservedConcurrentExecutions": 1,
        "Layers": Match.any_value()})

def test_core_table_export_creates_cross_stack_lock():
    _, core = _synth()
    # consuming core.table from another stack forces a CFN Output Export on the core stack
    core.has_output("*", {"Export": Match.any_value()})
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Move + convert `ServiceAid`** to `keri_cdk/service_aid.py`
```bash
git mv service-aid/serviceaid/cdk/service_aid_construct.py keri_cdk/service_aid.py
git mv service-aid/serviceaid/cdk/inception.py keri_cdk/_inception.py   # CR handler, referenced by service_aid.py
```
Changes to `keri_cdk/service_aid.py`:
- **Cross-stack lock:** replace the `core_table_name: str` prop + `ddb.Table.from_table_name(...)` with a `core_table: ddb.ITable` prop used directly (`core_table.table_name`, `core_table.table_arn`). Passing `core.table` from `KeriCoreStack` into a different stack makes CDK emit the `Export`/`Fn::ImportValue` lock automatically.
- **zip + layer:** replace the container `DockerImageFunction` with a zip `Function` (`code=Code.from_asset("keri_cdk/handlers/serviceaid")`, `handler="handler.handler"` — confirm the serviceaid handler entrypoint name, `runtime=PYTHON_3_13`, `architecture=ARM_64`, `layers=[runtime_layer or KeriRuntimeLayer(...).layer]`), `reserved_concurrent_executions=1`. The developer's `handler_module` (business compute) is supplied via env (`SERVICEAID_HANDLER`) as today; the example provides `gated_handler.py` in the function asset (the app bundles the example handler alongside).
- Keep the existing scoped IAM (keeper-secret `keri/{alias}/*`, the `dynamodb:LeadingKeys` core-table policy), the inception CR wiring, and the env. Export `ServiceAid` from `__init__.py`.

- [ ] **Step 4: Build the Gated Retrieval example** `examples/gated_retrieval/`

`gated_handler.py` — the made-up business compute (the `handler_module`): a function that, given the verified gated **request** exn, returns a generic `gated-record` payload ("cool data") that the framework issues as an ACDC. `schema/` holds the made-up `gated-access` + `gated-record` ACDC schemas (generic fictional fields). `app.py`:
```python
import aws_cdk as cdk
from keri_cdk import KeriCoreStack, ServiceAid

app = cdk.App()
core = KeriCoreStack(app, "KeriCore", table_name="keri-core")
svc = cdk.Stack(app, "GatedRetrieval")
ServiceAid(svc, "Gated", alias="gated", core_table=core.table,
           handler_module="gated_handler",
           allowlist=app.node.try_get_context("allowlist") or [])   # level-(a) gate
svc.add_dependency(core)
app.synth()
```
`cdk.json`: `{"app": "python app.py"}`.

- [ ] **Step 5: Example synth test** `tests/cdk/test_gated_example.py` — synth the example app and assert the ServiceAid function + the core-table lock export are present (mirror Step 1 asserts against the example app via `cdk.App()` + the app module, or `Template.from_stack`).

- [ ] **Step 6: Run → pass** (`tests/cdk/test_service_aid.py tests/cdk/test_gated_example.py`).
- [ ] **Step 7: Commit** `git commit -am "feat(keri_cdk): ServiceAid cross-stack core-table lock + zip/layer; Gated Retrieval example (allowlist gate)"`

---

## Task 7: `ecosystems/keri_host` app + `WatcherStack` seam

**Files:**
- Create: `ecosystems/keri_host/{app.py,cdk.json}`, `keri_cdk/watcher_stack.py`
- Test: `tests/cdk/test_keri_host_app.py`, `tests/cdk/test_watcher_seam.py`

- [ ] **Step 1: `WatcherStack` seam** `keri_cdk/watcher_stack.py`
```python
from aws_cdk import Stack
from constructs import Construct

class WatcherStack(Stack):
    """Seam for a future KEL-observing / duplicity-checking watcher. Phase B ships the
    construct API only — no handler. A future ecosystem composes a working watcher here."""
    def __init__(self, scope: Construct, cid: str, *, name: str, domain_name: str,
                 hosted_zone_id: str, witnesses=None, **kw):
        super().__init__(scope, cid, **kw)
        self.name = name
        raise NotImplementedError(
            "WatcherStack is a Phase-B seam: the watcher handler is a future build. "
            "See the CDK Phase B spec, Watcher seam.")
```
Export from `__init__.py`. Test `tests/cdk/test_watcher_seam.py`: importing + the exports work, and instantiation raises `NotImplementedError` (documents the seam).

- [ ] **Step 2: `ecosystems/keri_host/app.py`**
```python
import aws_cdk as cdk
from keri_cdk import WitnessStack, MailboxStack

app = cdk.App()
ctx = app.node.try_get_context
env = cdk.Environment(region=ctx("region") or "us-east-1")

WitnessStack(app, "KeriHostWitness", name="witness", alias="witness",
             domain_name=ctx("witness_domain"), hosted_zone_id=ctx("hosted_zone_id"),
             witness_url=f"https://{ctx('witness_domain')}", env=env)
MailboxStack(app, "KeriHostMailbox", name="mailbox", alias="mailbox",
             domain_name=ctx("mailbox_domain"), hosted_zone_id=ctx("hosted_zone_id"),
             mailbox_url=f"https://{ctx('mailbox_domain')}", env=env)
app.synth()
```
`cdk.json`: `{"app": "python app.py", "context": {}}` (domains supplied via `-c witness_domain=... -c mailbox_domain=... -c hosted_zone_id=...` at deploy). No `KeriCoreStack` (witness+mailbox own their tables).

- [ ] **Step 3: Test** `tests/cdk/test_keri_host_app.py` — synth the app, assert exactly two stacks (witness + mailbox), no `KeriCoreStack`, each with its own DynamoDB table.

- [ ] **Step 4: Run → pass; Commit** `git commit -am "feat(keri_cdk): keri_host ecosystem app (witness+mailbox) + WatcherStack seam"`

---

## Task 8: Full CDK assertion-test sweep

**Files:** `tests/cdk/` (consolidate/extend)

- [ ] **Step 1:** Ensure every stack has assertion coverage for the spec's guarantees: WitnessStack (reserved-concurrency=1, zip+layer, own table, scoped IAM), MailboxStack (LWA env, regional, streaming integration present, no reserved-concurrency, 2 layers), ServiceAid (reserved-concurrency=1, the core-table cross-stack export/lock, `dynamodb:LeadingKeys` policy, keeper-secret IAM), KeriCoreStack (PITR + deletion/termination protection), KeriRuntimeLayer (arm64/py313). Add any missing asserts.
- [ ] **Step 2: Run the whole CDK + serviceaid suite**

Run: `.venv/bin/python -m pytest tests/cdk/ tests/serviceaid/ -q` → all PASS.
- [ ] **Step 3: Commit** `git commit -am "test(keri_cdk): CDK assertion sweep across all stacks"`

---

## Task 9: Real-AWS deploy validation (both apps, personal/us-east-1)

**Files:** `keri_cdk/probes/deploy_validation/README.md` (records the runbook + results)

- [ ] **Step 1: Bootstrap CDK** (once) `AWS_PROFILE=personal npx cdk bootstrap aws://117870855864/us-east-1`
- [ ] **Step 2: Deploy + validate `ecosystems/keri_host`**

`AWS_PROFILE=personal npx cdk deploy --all -c witness_domain=<temp> -c mailbox_domain=<temp> -c hosted_zone_id=<zone>` (use temp/test subdomains for validation). Confirm: witness incepts + serves OOBI/receipts; mailbox comes up; **a mailbox SSE long-poll delivers a message** (deposit via `/fwd`, open the stream, observe the message — validates LWA + streaming). Record results in the README.
- [ ] **Step 3: Deploy + validate `examples/gated_retrieval`**

`AWS_PROFILE=personal npx cdk deploy --all` (from `examples/gated_retrieval`). Confirm: `KeriCoreStack` table up with PITR/deletion-protection; the **cross-stack export/lock** exists (the core stack shows an Export, and `cdk destroy` of the core stack is refused while the service imports — note the behavior); a **gated request exn → allowlist gate → gated-record ACDC** exchange completes e2e (reuse the existing Service-AID e2e path, re-themed).
- [ ] **Step 4: Tear down the validation stacks** (leave nothing running unless the user wants it): `cdk destroy --all` for both apps; confirm clean.
- [ ] **Step 5: Commit** the README with measured results. `git commit -am "docs(keri_cdk): real-AWS deploy validation results (keri_host + gated_retrieval)"`

---

## Task 10: Clean-slate cutover runbook + remove SAM dirs

**Files:** `keri_cdk/CUTOVER.md`; delete `sam-witness/`, `sam-mailbox/`, `service-aid/`

- [ ] **Step 1: Write `keri_cdk/CUTOVER.md`** — the deliberate, operator-run sequence to replace the live non-prod SAM stacks: (a) `sam delete` / `cdk destroy` the old witness/mailbox/watcher stacks + the throwaway core table; (b) `cdk deploy` the `keri_host` app + (if desired) the gated example on the REAL domains; (c) fresh AIDs (note: reuse saved federation salts by pre-seeding the keeper secrets if stable AIDs are wanted). Mark it operator-executed (not automated).
- [ ] **Step 2: Remove the superseded SAM + old-package dirs**
```bash
git rm -r sam-witness sam-mailbox
git rm -r service-aid     # serviceaid framework moved to keri_cdk/handlers/serviceaid; examples moved; cdk moved
```
Confirm `grep -rn "sam-witness\|sam-mailbox\|serviceaid\." --include=*.py keri_cdk ecosystems examples tests` finds no stale imports.
- [ ] **Step 3: Full suite green** `.venv/bin/python -m pytest tests/ -q` (CDK + serviceaid + dynamodbing) → PASS.
- [ ] **Step 4: Commit** `git commit -am "chore(keri_cdk): cutover runbook; remove superseded SAM dirs + old serviceaid package"`

---

## Final: whole-branch review + merge

- [ ] **Step 1:** Final reviewer over `development..HEAD`: spec coverage (library/app split, zip+layer, lock, reserved-concurrency, retries, mailbox streaming, watcher seam, extension-point API exposed not invented), no stale SAM refs, CDK assertions real, both real-AWS validations passed.
- [ ] **Step 2: Merge** ff to `development`, remove worktree + branch:
```bash
cd /Users/seriouscoderone/code/keripy
git merge --ff-only feat/cdk-phase-b
git worktree remove .worktrees/cdk-phaseB && git worktree prune
git branch -d feat/cdk-phase-b
```

---

## Self-Review Notes

- **Spec coverage:** library+app layout → Tasks 1,2,6,7; KeriCoreStack PITR/protection + lock → Tasks 1,6; handler relocation + responder retries → Task 2; zip+KeriRuntimeLayer (arm64, prebuilt, smoke) → Task 3; WitnessStack reserved-concurrency → Task 4; MailboxStack LWA/streaming → Task 5; ServiceAid lock + Gated Retrieval level-(a) → Task 6; keri_host app + watcher seam → Task 7; CDK assertions → Task 8; real-AWS validations → Task 9; clean-slate cutover → Task 10. Out-of-scope (level-b gate, full taxonomy, x86_64, publishing, Phase C) correctly absent.
- **Extension points:** exposed by relocating the serviceaid framework intact (Task 2) + ServiceAid construct props (Task 6); no NEW extension points invented (per spec).
- **Name consistency:** `KeriCoreStack`/`KeriRuntimeLayer`/`WitnessStack`/`MailboxStack`/`ServiceAid`/`WatcherStack`, `core_table` (ITable, not name), `handler_module`, `reserved_concurrent_executions=1`, `LD_LIBRARY_PATH=/opt/lib`, `keri_cdk/handlers/{witness,mailbox,serviceaid}` used consistently across tasks.
- **Verify-during-impl flags:** the serviceaid Lambda handler entrypoint symbol (`handler.handler`?), the mailbox ASGI `app` symbol for `run.sh`, the exact LWA zip wiring + layer ARN, whether the pinned `aws-cdk-lib` exposes `ResponseTransferMode.STREAM` (else CfnMethod fallback), and the libsodium `.so` path in the AL arm64 image — each has an explicit in-task note.
```
