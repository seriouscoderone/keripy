# CDK Phase C: Consolidate Services onto One Core Table (per-service isolation) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the witness and mailbox (and the watcher seam) on the shared `KeriCoreStack` DynamoDB table, each in its own `dynamodb:LeadingKeys`-isolated namespace — the Service-AID multi-tenant pattern extended to the infra tier — removing the per-service `{name}-db` tables.

**Architecture:** Each stack drops its own `ddb.Table` and takes a `core_table: ddb.ITable` passed from a `KeriCoreStack` in a different stack (emitting the CFN Export/`Fn::ImportValue` lifecycle lock). The Lambda's table IAM changes from full-table `grant_read_write_data` to a `LeadingKeys`-scoped policy over `{Aws.STACK_NAME}:*#*` + `__meta__#{Aws.STACK_NAME}:*`. The handler reads a `*_NAMESPACE` env var and passes `namespace=` into `DynamoDBer.open`. The Service-AID and `DynamoDBer` are unchanged.

**Tech Stack:** Python 3.14, aws-cdk-lib 2.259, `aws_cdk.assertions`, moto, pytest. keripy `keri_cdk/` library.

**Spec:** `docs/superpowers/specs/2026-06-14-cdk-phase-c-design.md`

---

### Task 0: Worktree venv + clean baseline

**Files:**
- Worktree: `~/code/keripy/.worktrees/cdk-phaseC` (branch `feat/cdk-phase-c`, already created)

- [ ] **Step 1: Create venv and install deps**

Run:
```bash
cd ~/code/keripy/.worktrees/cdk-phaseC
python3.14 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
.venv/bin/pip install aws-cdk-lib constructs moto boto3 pytest
```
Expected: installs succeed; `keri` installed editable.

- [ ] **Step 2: Run the baseline suites to confirm green start**

Run:
```bash
.venv/bin/python -m pytest tests/cdk tests/handlers tests/db -q
```
Expected: PASS (this is `development` tip `a9d11b54`; all green). Note the count.

- [ ] **Step 3: Commit nothing (venv is gitignored).** Proceed.

---

### Task 1: WitnessStack → core_table + LeadingKeys + namespace; add KeriCoreStack to keri_host app

**Files:**
- Modify: `keri_cdk/witness_stack.py` (remove table block `:70-82`; add `core_table` param; swap IAM `:121`; env `:108-118`; outputs `:217-229`)
- Modify: `ecosystems/keri_host/app.py` (add `KeriCoreStack`, wire witness)
- Test: `tests/cdk/test_witness_stack.py` (rewrite obsolete table test), `tests/cdk/test_keri_host_app.py` (witness parts)

- [ ] **Step 1: Rewrite `tests/cdk/test_witness_stack.py` to the pooled model (failing)**

Replace the whole file with:
```python
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import WitnessStack, KeriCoreStack

ENV = cdk.Environment(account="111111111111", region="us-east-1")


def _synth():
    app = cdk.App()
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=ENV)
    s = WitnessStack(app, "Wit", name="witness-test", alias="witness",
                     domain_name="witness.example.com", hosted_zone_id="Z123ABC456DEF7",
                     witness_url="https://witness.example.com",
                     core_table=core.table, env=ENV)
    return Template.from_stack(s)


def test_witness_zip_arm64_reserved_concurrency_layer():
    t = _synth()
    t.has_resource_properties("AWS::Lambda::Function", {
        "Architectures": ["arm64"],
        "ReservedConcurrentExecutions": 1,
        "Handler": "witness_handler.handler",
        "Runtime": "python3.14",
        "Layers": Match.any_value(),
        "Environment": {"Variables": Match.object_like({"LD_LIBRARY_PATH": "/opt/lib"})},
    })


def test_witness_owns_no_table():
    """Phase C: the witness no longer owns a Baser table — it uses the shared core table."""
    t = _synth()
    t.resource_count_is("AWS::DynamoDB::Table", 0)


def test_witness_leadingkeys_scoped_iam():
    """The witness's table IAM is LeadingKeys-scoped (not a full-table grant)."""
    t = _synth()
    t.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {"Statement":
        Match.array_with([Match.object_like({"Condition": Match.object_like({
            "ForAllValues:StringLike": Match.object_like({
                "dynamodb:LeadingKeys": Match.any_value()})})})])}})


def test_witness_namespace_env_present():
    t = _synth()
    t.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {"Variables": Match.object_like({"WITNESS_NAMESPACE": Match.any_value()})}})


def test_witness_imports_core_table_lock():
    """Witness stack references the core table cross-stack → Fn::ImportValue lifecycle lock."""
    import json
    body = json.dumps(_synth().to_json())
    assert "Fn::ImportValue" in body, "witness must import the core table (cross-stack lock)"


def test_witness_scoped_secretsmanager_iam():
    t = _synth()
    t.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {"Statement": Match.array_with([Match.object_like({
            "Action": Match.array_with(["secretsmanager:GetSecretValue"])})])}})
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/cdk/test_witness_stack.py -q`
Expected: FAIL — `WitnessStack` has no `core_table` param (TypeError) / still creates a table.

- [ ] **Step 3: Edit `keri_cdk/witness_stack.py` — add `core_table`, drop own table**

In `__init__`, change the signature to require `core_table` (keyword-only). Find:
```python
        witness_url: str,
        keeper_secret: str | None = None,
```
Replace with:
```python
        witness_url: str,
        core_table: "ddb.ITable",
        keeper_secret: str | None = None,
```

Delete the entire self-owned table block (currently around `:65-82`):
```python
        # --- DynamoDB Baser table -----------------------------------------------
        # ... comment ...
        self.baser = ddb.Table(
            self, "BaserTable", table_name=f"{name}-db",
            partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
        )
        self.baser.add_global_secondary_index(
            index_name="subdb-index",
            partition_key=ddb.Attribute(name="gsi_pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="gsi_sk", type=ddb.AttributeType.STRING),
        )
```
(remove it entirely).

- [ ] **Step 4: Edit `witness_stack.py` — env + IAM + outputs**

In the Lambda `environment={...}` dict, change the table var and add the namespace:
```python
                "WITNESS_BASER_TABLE": core_table.table_name,
                ...
                "WITNESS_NAMESPACE": f"{Aws.STACK_NAME}:kel",
```
(replace `self.baser.table_name` with `core_table.table_name`; add the `WITNESS_NAMESPACE` line).

Replace the table grant `self.baser.grant_read_write_data(self.fn)` with the LeadingKeys policy:
```python
        # Core (pooled) table: LeadingKeys-scoped to this stack's namespace only.
        self.fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:DescribeTable",
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:BatchWriteItem",
                ],
                resources=[
                    core_table.table_arn,
                    f"{core_table.table_arn}/index/*",
                ],
                conditions={
                    "ForAllValues:StringLike": {
                        "dynamodb:LeadingKeys": [
                            f"{Aws.STACK_NAME}:*#*",
                            f"__meta__#{Aws.STACK_NAME}:*",
                        ]
                    }
                },
            )
        )
```

Replace the `WitnessBaserTableName` output:
```python
        CfnOutput(self, "WitnessNamespace", value=f"{Aws.STACK_NAME}:kel")
```
(was `CfnOutput(self, "WitnessBaserTableName", value=self.baser.table_name)`).

- [ ] **Step 5: Run witness stack tests to verify pass**

Run: `.venv/bin/python -m pytest tests/cdk/test_witness_stack.py -q`
Expected: PASS.

- [ ] **Step 6: Add `KeriCoreStack` to `ecosystems/keri_host/app.py` and wire the witness**

In `ecosystems/keri_host/app.py`, change the import and composition. Find:
```python
from keri_cdk import WitnessStack, MailboxStack
```
Replace with:
```python
from keri_cdk import WitnessStack, MailboxStack, KeriCoreStack
```
After the `env = cdk.Environment(...)` line and before the `WitnessStack(...)` call, add:
```python
core = KeriCoreStack(app, "KeriHostCore", table_name="keri-core", env=env)
```
Add `core_table=core.table` to the `WitnessStack(...)` call (keep all other args):
```python
WitnessStack(app, "KeriHostWitness",
             name=witness_name,
             alias="witness",
             domain_name=witness_domain,
             hosted_zone_id=hosted_zone_id,
             witness_url=f"https://{witness_domain}",
             core_table=core.table,
             env=env)
```
(Leave the `MailboxStack(...)` call unchanged for now — it still owns its table until Task 2.)

- [ ] **Step 7: Update `tests/cdk/test_keri_host_app.py` — witness parts**

Replace `test_keri_host_is_witness_plus_mailbox_no_core` and `test_witness_baser_table_name` with:
```python
def test_keri_host_witness_uses_core_table_no_own_table():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=env)
    w = WitnessStack(app, "W", name="witness", alias="witness",
                     domain_name="w.ex.com", hosted_zone_id="Z123ABC456DEF7",
                     witness_url="https://w.ex.com", core_table=core.table, env=env)
    tw = Template.from_stack(w)
    tw.resource_count_is("AWS::DynamoDB::Table", 0)
    tc = Template.from_stack(core)
    tc.has_resource_properties("AWS::DynamoDB::Table", {"TableName": "keri-core"})
```
Update the import line at the top:
```python
from keri_cdk import WitnessStack, MailboxStack, KeriCoreStack
```
(Leave `test_mailbox_baser_table_name` for Task 2.)

- [ ] **Step 8: Run the CDK suite**

Run: `.venv/bin/python -m pytest tests/cdk -q`
Expected: PASS except the still-present mailbox table tests (those are Task 2). Confirm witness + keri_host witness tests pass and no witness table is created.

- [ ] **Step 9: Commit**

```bash
git add keri_cdk/witness_stack.py ecosystems/keri_host/app.py tests/cdk/test_witness_stack.py tests/cdk/test_keri_host_app.py
git commit -m "feat(cdk): witness pools onto shared core table (LeadingKeys-scoped namespace)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: MailboxStack → core_table + LeadingKeys + namespace; wire keri_host app

**Files:**
- Modify: `keri_cdk/mailbox_stack.py` (remove table `:100-112`; add `core_table`; swap IAM `:164`; env `:142-160`; outputs `:242-245`)
- Modify: `ecosystems/keri_host/app.py` (wire mailbox)
- Test: `tests/cdk/test_mailbox_stack.py`, `tests/cdk/test_keri_host_app.py` (mailbox parts)

- [ ] **Step 1: Rewrite `tests/cdk/test_mailbox_stack.py` to the pooled model (failing)**

Open `tests/cdk/test_mailbox_stack.py`. Update its `_synth()` to build a `KeriCoreStack` and pass `core_table=core.table` + `env=ENV` (mirror Task 1's witness `_synth`). Replace any "owns a table"/`resource_count_is("AWS::DynamoDB::Table", 1)` assertion with:
```python
def test_mailbox_owns_no_table():
    t = _synth()
    t.resource_count_is("AWS::DynamoDB::Table", 0)


def test_mailbox_leadingkeys_scoped_iam():
    t = _synth()
    t.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {"Statement":
        Match.array_with([Match.object_like({"Condition": Match.object_like({
            "ForAllValues:StringLike": Match.object_like({
                "dynamodb:LeadingKeys": Match.any_value()})})})])}})


def test_mailbox_namespace_env_present():
    t = _synth()
    t.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {"Variables": Match.object_like({"MAILBOX_NAMESPACE": Match.any_value()})}})


def test_mailbox_imports_core_table_lock():
    import json
    body = json.dumps(_synth().to_json())
    assert "Fn::ImportValue" in body, "mailbox must import the core table (cross-stack lock)"
```
Keep the existing streaming/two-layer/no-reserved-concurrency assertions; ensure `_synth()` passes `core_table` + `env` and the file imports `KeriCoreStack` and `Match`. Add at top:
```python
from keri_cdk import MailboxStack, KeriCoreStack
ENV = cdk.Environment(account="111111111111", region="us-east-1")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/cdk/test_mailbox_stack.py -q`
Expected: FAIL — `MailboxStack` has no `core_table` param / still creates a table.

- [ ] **Step 3: Edit `keri_cdk/mailbox_stack.py` — add `core_table`, drop own table**

In `__init__`, add `core_table` as required keyword-only. Find:
```python
        mailbox_url: str,
        witness_aid: str = "",
```
Replace with:
```python
        mailbox_url: str,
        core_table: "ddb.ITable",
        witness_aid: str = "",
```
Delete the self-owned table block (`:96-112`, the `self.baser = ddb.Table(... "BaserTable" ...)` plus its `add_global_secondary_index`).

- [ ] **Step 4: Edit `mailbox_stack.py` — env + IAM + outputs**

In the `environment={...}` dict, change the table var and add the namespace:
```python
                "MAILBOX_BASER_TABLE": core_table.table_name,
                ...
                "MAILBOX_NAMESPACE": f"{Aws.STACK_NAME}:mbx",
```
Replace `self.baser.grant_read_write_data(self.fn)` with the LeadingKeys policy:
```python
        self.fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:DescribeTable",
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:BatchWriteItem",
                ],
                resources=[
                    core_table.table_arn,
                    f"{core_table.table_arn}/index/*",
                ],
                conditions={
                    "ForAllValues:StringLike": {
                        "dynamodb:LeadingKeys": [
                            f"{Aws.STACK_NAME}:*#*",
                            f"__meta__#{Aws.STACK_NAME}:*",
                        ]
                    }
                },
            )
        )
```
Replace the `MailboxBaserTableName` output:
```python
        CfnOutput(self, "MailboxNamespace", value=f"{Aws.STACK_NAME}:mbx")
```

- [ ] **Step 5: Wire the mailbox in `ecosystems/keri_host/app.py`**

Add `core_table=core.table` to the `MailboxStack(...)` call (the `core` var already exists from Task 1):
```python
MailboxStack(app, "KeriHostMailbox",
             name=mailbox_name,
             alias="mailbox",
             domain_name=mailbox_domain,
             hosted_zone_id=hosted_zone_id,
             mailbox_url=f"https://{mailbox_domain}",
             core_table=core.table,
             lwa_layer_arn=lwa_layer_arn,
             env=env)
```

- [ ] **Step 6: Update `tests/cdk/test_keri_host_app.py` — replace `test_mailbox_baser_table_name`**

Replace it with:
```python
def test_keri_host_mailbox_uses_core_table_no_own_table():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=env)
    m = MailboxStack(app, "M", name="mailbox", alias="mailbox",
                     domain_name="m.ex.com", hosted_zone_id="Z123ABC456DEF7",
                     mailbox_url="https://m.ex.com", core_table=core.table, env=env)
    Template.from_stack(m).resource_count_is("AWS::DynamoDB::Table", 0)
```

- [ ] **Step 7: Run the CDK suite — all green now**

Run: `.venv/bin/python -m pytest tests/cdk -q`
Expected: PASS (all CDK tests, including the unchanged `test_service_aid.py` and `test_gated_example.py`).

- [ ] **Step 8: Commit**

```bash
git add keri_cdk/mailbox_stack.py ecosystems/keri_host/app.py tests/cdk/test_mailbox_stack.py tests/cdk/test_keri_host_app.py
git commit -m "feat(cdk): mailbox pools onto shared core table (LeadingKeys-scoped namespace)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Handlers read `*_NAMESPACE` and pass `namespace=` into `DynamoDBer.open`

**Files:**
- Modify: `keri_cdk/handlers/witness/witness_handler.py` (`:73` env reads, `:95` open)
- Modify: `keri_cdk/handlers/mailbox/mailbox_handler.py` (`:162-186`)
- Test: `tests/handlers/test_handler_namespace.py` (new)

- [ ] **Step 1: Write the failing test `tests/handlers/test_handler_namespace.py`**

```python
"""The witness/mailbox handlers resolve their pooled-table namespace from env."""
import importlib


def test_witness_namespace_from_env(monkeypatch):
    monkeypatch.setenv("WITNESS_NAMESPACE", "KeriHostWitness:kel")
    wh = importlib.import_module("keri_cdk.handlers.witness.witness_handler")
    assert wh._namespace("witness") == "KeriHostWitness:kel"


def test_witness_namespace_default(monkeypatch):
    monkeypatch.delenv("WITNESS_NAMESPACE", raising=False)
    wh = importlib.import_module("keri_cdk.handlers.witness.witness_handler")
    assert wh._namespace("witness") == "witness:kel"


def test_mailbox_namespace_from_env(monkeypatch):
    monkeypatch.setenv("MAILBOX_NAMESPACE", "KeriHostMailbox:mbx")
    mh = importlib.import_module("keri_cdk.handlers.mailbox.mailbox_handler")
    assert mh._namespace("mailbox") == "KeriHostMailbox:mbx"


def test_mailbox_namespace_default(monkeypatch):
    monkeypatch.delenv("MAILBOX_NAMESPACE", raising=False)
    mh = importlib.import_module("keri_cdk.handlers.mailbox.mailbox_handler")
    assert mh._namespace("mailbox") == "mailbox:mbx"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/handlers/test_handler_namespace.py -q`
Expected: FAIL — `_namespace` attribute does not exist.

- [ ] **Step 3: Add `_namespace` helper + use it in `witness_handler.py`**

Near the top of `keri_cdk/handlers/witness/witness_handler.py` (module level, after imports), add:
```python
def _namespace(name):
    """Pooled-table namespace for this witness (env-driven; defaults to name)."""
    return os.environ.get("WITNESS_NAMESPACE") or f"{name}:kel"
```
At the `DynamoDBer.open(...)` call (`:95`), add the `namespace=` argument:
```python
    db = DynamoDBer.open(name=name, stores=BASER_STORES, table_name=baser_table,
                         namespace=_namespace(name), **kwa)
```

- [ ] **Step 4: Add `_namespace` helper + use it in `mailbox_handler.py`**

Add (module level, after imports):
```python
def _namespace(name):
    """Pooled-table namespace for this mailbox (env-driven; defaults to name)."""
    return os.environ.get("MAILBOX_NAMESPACE") or f"{name}:mbx"
```
At the `DynamoDBer.open(...)` call (`:186`), add `namespace=`:
```python
    db = DynamoDBer.open(name=name, stores=baser_and_mbx_stores,
                         table_name=baser_table, namespace=_namespace(name), **kwa)
```
(Confirm `import os` is present at the top of each handler — it is.)

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/handlers/test_handler_namespace.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the full handler suite (no regression)**

Run: `.venv/bin/python -m pytest tests/handlers -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add keri_cdk/handlers/witness/witness_handler.py keri_cdk/handlers/mailbox/mailbox_handler.py tests/handlers/test_handler_namespace.py
git commit -m "feat(cdk): witness/mailbox handlers pass pooled-table namespace into DynamoDBer.open

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: WatcherStack seam accepts `core_table`

**Files:**
- Modify: `keri_cdk/watcher_stack.py`
- Test: `tests/cdk/test_watcher_seam.py`

- [ ] **Step 1: Update `tests/cdk/test_watcher_seam.py` to pass `core_table` (failing)**

Open `tests/cdk/test_watcher_seam.py`. Ensure the test that constructs `WatcherStack` passes a `core_table` kwarg and still expects `NotImplementedError`. Replace the construction call with:
```python
import pytest
import aws_cdk as cdk
from keri_cdk import WatcherStack, KeriCoreStack


def test_watcher_is_seam_not_implemented():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=env)
    with pytest.raises(NotImplementedError):
        WatcherStack(app, "Wat", name="watcher", domain_name="wat.ex.com",
                     hosted_zone_id="Z123ABC456DEF7", core_table=core.table, env=env)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/cdk/test_watcher_seam.py -q`
Expected: FAIL — `WatcherStack` does not accept `core_table`.

- [ ] **Step 3: Edit `keri_cdk/watcher_stack.py`**

Replace the file body with:
```python
from aws_cdk import Stack, Aws
from constructs import Construct
from aws_cdk import aws_dynamodb as ddb


class WatcherStack(Stack):
    """Seam for a future KEL-observing / duplicity-checking watcher. Phase C ships the
    construct API only — no handler. When built, the watcher pools its Baser onto the
    shared ``core_table`` under namespace ``<stack-name>:kel`` with the same
    LeadingKeys grant (``<stack-name>:*#*``) as the witness — see
    docs/superpowers/specs/2026-06-14-cdk-phase-c-design.md."""

    def __init__(self, scope: Construct, cid: str, *, name: str, domain_name: str,
                 hosted_zone_id: str, core_table: "ddb.ITable", witnesses=None, **kw):
        super().__init__(scope, cid, **kw)
        self.name = name
        raise NotImplementedError(
            "WatcherStack is a seam: the watcher handler is a future build. It will pool "
            f"onto core_table under namespace {Aws.STACK_NAME}:kel.")
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/cdk/test_watcher_seam.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add keri_cdk/watcher_stack.py tests/cdk/test_watcher_seam.py
git commit -m "feat(cdk): watcher seam takes core_table (pools onto shared table when built)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: DynamoDBer namespace isolation test (witness/mailbox/Reger coexist, no collision)

**Files:**
- Test: `tests/db/test_dynamodbing_namespace.py` (append)

- [ ] **Step 1: Append the isolation test (failing only if behavior regresses)**

Add to `tests/db/test_dynamodbing_namespace.py`:
```python
@needs_moto
def test_witness_mailbox_reger_namespaces_isolated_on_one_table():
    """Witness (:kel), mailbox (:mbx) and a Service-AID Reger (:tel) namespaces on the
    SAME core table read only their own rows — the Phase C per-service isolation."""
    from moto import mock_aws
    with mock_aws():
        wit = DynamoDBer.open(name="witness", stores=["kels."], region="us-east-1",
                              table_name="keri-core", namespace="KeriHostWitness:kel")
        mbx = DynamoDBer.open(name="mailbox", stores=["kels."], region="us-east-1",
                              table_name="keri-core", namespace="KeriHostMailbox:mbx")
        reg = DynamoDBer.open(name="gated", stores=["kels."], region="us-east-1",
                              table_name="keri-core", namespace="gated:tel")
        wsub = wit.env.open_db(b"kels.")
        msub = mbx.env.open_db(b"kels.")
        rsub = reg.env.open_db(b"kels.")
        wit.setVal(wsub, b"AID", b"witness-row")
        mbx.setVal(msub, b"AID", b"mailbox-row")
        reg.setVal(rsub, b"AID", b"reger-row")
        # Same subdb + same key, three namespaces — each isolated.
        assert wit.getVal(wsub, b"AID") == b"witness-row"
        assert mbx.getVal(msub, b"AID") == b"mailbox-row"
        assert reg.getVal(rsub, b"AID") == b"reger-row"
        wit.close(); mbx.close(); reg.close()
```

- [ ] **Step 2: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/db/test_dynamodbing_namespace.py -q`
Expected: PASS (DynamoDBer already supports `namespace`; this pins the Phase C contract).

- [ ] **Step 3: Run the full local suite**

Run: `.venv/bin/python -m pytest tests/cdk tests/handlers tests/db -q`
Expected: PASS (all green).

- [ ] **Step 4: Commit**

```bash
git add tests/db/test_dynamodbing_namespace.py
git commit -m "test(cdk): pin per-service namespace isolation on the shared core table

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Real-AWS temporary deploy + LeadingKeys probe + teardown

**Files:**
- Use: `keri_cdk/layers/build_layer.sh`, `ecosystems/keri_host/`, `keri_cdk/probes/leadingkeys/`
- No code changes (validation task). Requires Docker + `AWS_PROFILE=personal`.

- [ ] **Step 1: Build the runtime layer (Docker)**

Run:
```bash
cd ~/code/keripy/.worktrees/cdk-phaseC
bash keri_cdk/layers/build_layer.sh
```
Expected: produces the prebuilt layer asset (gitignored) for arm64/python3.14.

- [ ] **Step 2: Deploy `keri_host` to temporary domains on `personal`**

Run (use temp subdomains so the live federation is untouched; pass a current LWA layer version):
```bash
cd ~/code/keripy/.worktrees/cdk-phaseC/ecosystems/keri_host
AWS_PROFILE=personal cdk deploy --all \
  -c region=us-east-1 \
  -c witness_domain=witc.keri.host \
  -c mailbox_domain=mboxc.keri.host \
  -c hosted_zone_id=<keri.host hosted zone id> \
  -c lwa_layer_arn=arn:aws:lambda:us-east-1:753240598075:layer:LambdaAdapterLayerArm64:<current>
```
Expected: `KeriHostCore`, `KeriHostWitness`, `KeriHostMailbox` deploy. Exactly **one** `keri-core` table exists; the witness/mailbox stacks create no table.

- [ ] **Step 3: Verify witness + mailbox from the shared table**

- `curl https://witc.keri.host/oobi/<witness-aid>` returns the witness OOBI (incept/sign worked from the pooled table).
- Confirm the witness AID's KEL rows exist under PK prefix `KeriHostWitness:kel#...` in the `keri-core` table (DynamoDB console or `aws dynamodb query`).
- `curl -N https://mboxc.keri.host/...` SSE endpoint streams a keepalive (mailbox streaming intact); mailbox rows under `KeriHostMailbox:mbx#...`.

- [ ] **Step 4: Re-run the LeadingKeys probe against the per-service boundary**

Run the probe at `keri_cdk/probes/leadingkeys/` (see its README) with the Phase C namespaces, confirming a role scoped to `KeriHostWitness:*#*` is **DENIED** reads of `KeriHostMailbox:mbx#...` and of a `gated:tel#...` Reger namespace, and **ALLOWED** its own `KeriHostWitness:kel#...`. Record the result in the probe README/run log.

- [ ] **Step 5: Tear down the temporary stacks**

Run:
```bash
cd ~/code/keripy/.worktrees/cdk-phaseC/ecosystems/keri_host
AWS_PROFILE=personal cdk destroy KeriHostWitness KeriHostMailbox
# KeriHostCore has deletion/termination protection; leave it OR remove protection + destroy if a clean slate is wanted.
```
Expected: witness/mailbox stacks removed; the live SAM federation untouched throughout.

- [ ] **Step 6: Commit any probe-log/README updates**

```bash
git add keri_cdk/probes/leadingkeys/
git commit -m "test(cdk): re-run LeadingKeys probe for Phase C per-service boundary (real AWS)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Completion

After all tasks: run `.venv/bin/python -m pytest tests/cdk tests/handlers tests/db -q` (all green), then use **superpowers:finishing-a-development-branch** to merge `feat/cdk-phase-c` directly into `development` (matches Phase A/B; no PR).
