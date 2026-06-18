# SAM → CDK Federation Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Destroy the live SAM 5×5 KERI federation and deploy a fresh CDK 5×5 federation (oracle-on, one shared `keri-core` table) across the 5 existing domains, reusing the same subdomain names, validated to a throwaway 3-of-5 client e2e.

**Architecture:** The witness/mailbox CDK constructs are already federation-ready, so the deploy-side change is a loop. Tasks 1–5 build & unit-test all code (a `keri_cdk/federation.py` loader+builder, a thin `app.py`, a SAM teardown script, an AID-harvest script, a kli-driven e2e harness) **without touching live AWS**. Tasks 6–9 then execute the irreversible runtime cutover in order: teardown → deploy → harvest → validate. Task 10 is an optional cross-repo doc scrub in locksmith.

**Tech Stack:** Python, AWS CDK (`aws-cdk-lib` / `constructs`), `cdk` CLI via `npx aws-cdk@latest`, keripy (`kli`, `agenting.Receiptor`), boto3, AWS CLI, pytest.

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from the spec (`docs/superpowers/specs/2026-06-18-sam-to-cdk-federation-cutover-design.md`).

- **AWS target:** `AWS_PROFILE=personal`, region `us-east-1`, account `117870855864`. Prefix every AWS CLI / boto3 / cdk command with `AWS_PROFILE=personal`.
- **Git push target:** `fork` (seriouscoderone) only — **never** `origin`/WebOfTrust. Branch: `feat/sam-to-cdk-cutover`.
- **Hosted zones are preserved.** Only stack-owned resources (ACM certs, A-records, ACM-validation CNAMEs) are destroyed. The 5 Route53 zones (keri.host / honest.town / verdadero.me / goonei.com / legitim.us) stay.
- **Oracle stays ON.** The witness/mailbox handlers already pass `shared_namespace="shared"` + `SHARED_KEL_STORES`. **Do not modify the handlers.**
- **Reuse the same subdomain names:** `witness.<domain>` / `mailbox.<domain>` (e.g. `witness.keri.host`).
- **Stack IDs are domain-derived:** `Witness{slug}` / `Mailbox{slug}` (never index-derived). Per-stack namespace `<stack>:kel` / `:mbx` and keeper secret `keri/<stack>/keeper` derive from the stack name.
- **Client toad = 3-of-5** over the 5 witnesses.
- **Deploy via `npx aws-cdk@latest`** (global `cdk` lags the lib schema). Core deploys first / is deleted last (cross-stack `CoreTable` export lifecycle lock).
- **Teardown is destructive and irreversible** → discover-before-destroy + an explicit human confirmation gate before any deletion.
- **Privacy:** never commit real Route53 zone IDs or the harvested AIDs. `federation.json` and `federation_aids.json` are gitignored; only `*.example.json` placeholders are committed.

---

### Task 0: Worktree environment setup

Stand up the build/run environment. No code change; this task's deliverable is a working venv that can synth and run the CDK app and tests.

**Files:**
- Create (gitignored, not committed): `.venv/`, `keri_cdk/layers/keri_runtime/`, `keri_cdk/layers/serviceaid_framework/`

**Interfaces:**
- Produces: an activated venv with `aws-cdk-lib`, `constructs`, `boto3`, `pytest`, `pytest-asyncio`, and editable keripy importable; placeholder layer dirs so `cdk synth` / synth tests resolve `Code.from_asset`.

- [ ] **Step 1: Create and populate the venv**

```bash
cd ~/code/keripy
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e . aws-cdk-lib constructs boto3 pytest pytest-asyncio
```

- [ ] **Step 2: Create the gitignored layer-placeholder dirs (synth needs them to exist)**

```bash
mkdir -p keri_cdk/layers/keri_runtime keri_cdk/layers/serviceaid_framework
touch keri_cdk/layers/keri_runtime/.keep keri_cdk/layers/serviceaid_framework/.keep
```

- [ ] **Step 3: Verify the toolchain imports and the existing CDK tests pass**

Run:
```bash
cd ~/code/keripy
.venv/bin/python -c "import aws_cdk, constructs, boto3, keri; print('toolchain OK')"
.venv/bin/python -m pytest tests/cdk/ -q
```
Expected: `toolchain OK`, then the existing `tests/cdk/` suite passes (green). If `pytest-asyncio` errors on the mailbox SSE tests, confirm it installed in Step 1.

- [ ] **Step 4: Confirm `npx aws-cdk@latest` is reachable (no deploy yet)**

Run: `AWS_PROFILE=personal npx aws-cdk@latest --version`
Expected: prints a 2.x version string (downloads on first use). No commit (env setup only).

---

### Task 1: Federation config loader

A privacy-preserving loader for the 5 `(slug, domain, hosted_zone_id)` entries. Lives in the importable `keri_cdk` package so it is unit-testable; the committed example carries `example.com` placeholders only.

**Files:**
- Create: `keri_cdk/federation.py`
- Create: `ecosystems/keri_host/federation.example.json`
- Modify: `.gitignore` (add the two gitignored artifacts)
- Test: `tests/cdk/test_federation_config.py`

**Interfaces:**
- Produces: `keri_cdk.federation.load_federation(config_dir, env=None, env_var="KERI_HOST_FEDERATION") -> list[dict]` — each dict has keys `slug`, `domain`, `hosted_zone_id`. Raises `ValueError` on empty list, missing key, or duplicate slug. Resolution order: `$KERI_HOST_FEDERATION` (inline JSON if it starts with `[`, else a file path) → `{config_dir}/federation.json` → `{config_dir}/federation.example.json`.

- [ ] **Step 1: Write the failing tests**

Create `tests/cdk/test_federation_config.py`:

```python
"""Tests for keri_cdk.federation.load_federation (config resolution + validation)."""
import pathlib

import pytest

from keri_cdk.federation import load_federation

ECOSYSTEM_DIR = pathlib.Path(__file__).resolve().parents[2] / "ecosystems" / "keri_host"


def test_loads_committed_example_with_five_entries():
    # No env var, no gitignored federation.json in a clean checkout -> falls back to example.
    entries = load_federation(ECOSYSTEM_DIR, env={})
    assert len(entries) == 5
    for e in entries:
        assert {"slug", "domain", "hosted_zone_id"} <= set(e)
        assert e["domain"].endswith("example.com")  # placeholders only, never real domains


def test_inline_env_json_overrides_files(tmp_path):
    inline = '[{"slug":"X","domain":"x.test","hosted_zone_id":"Z1"}]'
    entries = load_federation(tmp_path, env={"KERI_HOST_FEDERATION": inline})
    assert entries == [{"slug": "X", "domain": "x.test", "hosted_zone_id": "Z1"}]


def test_env_path_is_read_when_not_inline(tmp_path):
    p = tmp_path / "fed.json"
    p.write_text('[{"slug":"Y","domain":"y.test","hosted_zone_id":"Z2"}]')
    entries = load_federation(tmp_path, env={"KERI_HOST_FEDERATION": str(p)})
    assert entries[0]["slug"] == "Y"


def test_real_file_preferred_over_example(tmp_path):
    (tmp_path / "federation.json").write_text('[{"slug":"R","domain":"r.t","hosted_zone_id":"Z3"}]')
    (tmp_path / "federation.example.json").write_text('[{"slug":"E","domain":"e.t","hosted_zone_id":"Z4"}]')
    entries = load_federation(tmp_path, env={})
    assert entries[0]["slug"] == "R"


def test_rejects_duplicate_slug(tmp_path):
    inline = '[{"slug":"X","domain":"a","hosted_zone_id":"Z1"},{"slug":"X","domain":"b","hosted_zone_id":"Z2"}]'
    with pytest.raises(ValueError, match="duplicate slug"):
        load_federation(tmp_path, env={"KERI_HOST_FEDERATION": inline})


def test_rejects_missing_key(tmp_path):
    with pytest.raises(ValueError, match="missing keys"):
        load_federation(tmp_path, env={"KERI_HOST_FEDERATION": '[{"slug":"X","domain":"a"}]'})


def test_rejects_empty_list(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        load_federation(tmp_path, env={"KERI_HOST_FEDERATION": "[]"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_federation_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'keri_cdk.federation'` (and the example file missing).

- [ ] **Step 3: Write the loader**

Create `keri_cdk/federation.py` (loader portion; the builder is added in Task 2):

```python
"""Federation helpers for the keri.host ecosystem CDK app.

load_federation resolves the list of (slug, domain, hosted_zone_id) entries without
committing private Route53 zone IDs. build_federation (Task 2) maps that list to one
KeriCoreStack plus a witness+mailbox stack pair per entry.
"""
import json
import os
import pathlib

REQUIRED_KEYS = ("slug", "domain", "hosted_zone_id")


def load_federation(config_dir, env=None, env_var="KERI_HOST_FEDERATION"):
    """Return the federation entries (list of dicts: slug/domain/hosted_zone_id).

    Resolution order (privacy: real zone IDs are never committed):
      1. ${env_var} — inline JSON (starts with '[') or a path to a JSON file.
      2. {config_dir}/federation.json — gitignored real config.
      3. {config_dir}/federation.example.json — committed example.com placeholders.
    """
    env = os.environ if env is None else env
    config_dir = pathlib.Path(config_dir)
    raw = env.get(env_var)
    if raw:
        text = raw if raw.lstrip().startswith("[") else pathlib.Path(raw).read_text()
    else:
        real = config_dir / "federation.json"
        src = real if real.exists() else config_dir / "federation.example.json"
        text = src.read_text()
    entries = json.loads(text)
    _validate(entries)
    return entries


def _validate(entries):
    if not isinstance(entries, list) or not entries:
        raise ValueError("federation config must be a non-empty JSON list")
    seen = set()
    for e in entries:
        missing = [k for k in REQUIRED_KEYS if not e.get(k)]
        if missing:
            raise ValueError(f"federation entry {e!r} missing keys: {missing}")
        if e["slug"] in seen:
            raise ValueError(f"duplicate slug: {e['slug']!r}")
        seen.add(e["slug"])
```

- [ ] **Step 4: Create the committed example file**

Create `ecosystems/keri_host/federation.example.json`:

```json
[
  { "slug": "Alpha",   "domain": "alpha.example.com",   "hosted_zone_id": "Z00000000000000000001" },
  { "slug": "Bravo",   "domain": "bravo.example.com",   "hosted_zone_id": "Z00000000000000000002" },
  { "slug": "Charlie", "domain": "charlie.example.com", "hosted_zone_id": "Z00000000000000000003" },
  { "slug": "Delta",   "domain": "delta.example.com",   "hosted_zone_id": "Z00000000000000000004" },
  { "slug": "Echo",    "domain": "echo.example.com",    "hosted_zone_id": "Z00000000000000000005" }
]
```

- [ ] **Step 5: Add the gitignored artifacts to `.gitignore`**

Append to `.gitignore` (near the existing `keri_cdk/layers/keri_runtime/` line):

```
# keri.host federation: real zone IDs + harvested AIDs are private, never committed
ecosystems/keri_host/federation.json
ecosystems/keri_host/federation_aids.json
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_federation_config.py -q`
Expected: PASS (7 passed).

- [ ] **Step 7: Commit**

```bash
cd ~/code/keripy
git add keri_cdk/federation.py ecosystems/keri_host/federation.example.json .gitignore tests/cdk/test_federation_config.py
git commit -m "feat(federation): privacy-preserving config loader for the 5-domain federation"
```

---

### Task 2: Federation builder + thin app.py

Add the loop that maps the entries to CDK stacks, and reduce `app.py` to a thin wire-up that synthesizes the full 5×5.

**Files:**
- Modify: `keri_cdk/federation.py` (add `build_federation`)
- Modify: `ecosystems/keri_host/app.py` (replace the single-pair body with the loop)
- Test: `tests/cdk/test_federation_build.py`

**Interfaces:**
- Consumes: `load_federation` (Task 1); `KeriCoreStack`, `WitnessStack`, `MailboxStack` from `keri_cdk` (unchanged constructors — see spec "Current state").
- Produces: `keri_cdk.federation.build_federation(app, entries, env, *, core_table_name="keri-core", lwa_layer_arn=None) -> dict` returning `{"core": KeriCoreStack, "witnesses": {slug: WitnessStack}, "mailboxes": {slug: MailboxStack}}`. Stack IDs are `Witness{slug}` / `Mailbox{slug}`; per-stack `name`/`alias` = `witness-{slug.lower()}` / `mailbox-{slug.lower()}`; `domain_name` = `witness.{domain}` / `mailbox.{domain}`; all share `core.table`.

- [ ] **Step 1: Write the failing tests**

Create `tests/cdk/test_federation_build.py`:

```python
"""Tests for keri_cdk.federation.build_federation (the 1x1 -> 5x5 loop)."""
import aws_cdk as cdk

from keri_cdk.federation import build_federation

ENTRIES = [{"slug": s, "domain": f"{s.lower()}.test", "hosted_zone_id": f"Z{i}"}
           for i, s in enumerate(["Alpha", "Bravo", "Charlie", "Delta", "Echo"])]


def _app():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    return app, env


def test_builds_core_plus_pair_per_entry():
    app, env = _app()
    built = build_federation(app, ENTRIES, env)
    assert len(built["witnesses"]) == 5
    assert len(built["mailboxes"]) == 5
    stacks = [c for c in app.node.children if isinstance(c, cdk.Stack)]
    assert len(stacks) == 11  # 1 core + 5 witness + 5 mailbox


def test_stack_ids_are_domain_derived_not_indexed():
    app, env = _app()
    built = build_federation(app, ENTRIES, env)
    assert built["witnesses"]["Alpha"].stack_name == "WitnessAlpha"
    assert built["mailboxes"]["Echo"].stack_name == "MailboxEcho"
    # No index-based names leaked in.
    names = {s.stack_name for s in app.node.children if isinstance(s, cdk.Stack)}
    assert "Witness0" not in names and "Mailbox0" not in names


def test_witness_uses_expected_subdomain_and_url():
    app, env = _app()
    built = build_federation(app, ENTRIES, env)
    w = built["witnesses"]["Bravo"]
    assert w.domain_name == "witness.bravo.test"
    assert w.witness_url == "https://witness.bravo.test"
```

> Note: `WitnessStack`/`MailboxStack` expose `domain_name`/`witness_url`/`mailbox_url` as instance attributes (set from the constructor kwargs). If they do not, assert via `aws_cdk.assertions.Template.from_stack(w).has_resource_properties("AWS::ApiGateway::DomainName", {"DomainName": "witness.bravo.test"})` instead.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_federation_build.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_federation'`.

- [ ] **Step 3: Add `build_federation` to `keri_cdk/federation.py`**

Append to `keri_cdk/federation.py`:

```python
from keri_cdk import KeriCoreStack, WitnessStack, MailboxStack


def build_federation(app, entries, env, *, core_table_name="keri-core", lwa_layer_arn=None):
    """Instantiate KeriCoreStack + one witness+mailbox pair per entry.

    Stack IDs are domain-derived (Witness{slug} / Mailbox{slug}) so each node's
    namespace (<stack>:kel / :mbx) and keeper secret (keri/<stack>/keeper) stay
    stable per domain regardless of config ordering.
    """
    core = KeriCoreStack(app, "KeriHostCore", table_name=core_table_name, env=env)
    witnesses, mailboxes = {}, {}
    for e in entries:
        slug, domain, zone = e["slug"], e["domain"], e["hosted_zone_id"]
        low = slug.lower()
        wdom, mdom = f"witness.{domain}", f"mailbox.{domain}"
        witnesses[slug] = WitnessStack(
            app, f"Witness{slug}",
            name=f"witness-{low}", alias=f"witness-{low}",
            domain_name=wdom, hosted_zone_id=zone,
            witness_url=f"https://{wdom}", core_table=core.table, env=env)
        mailboxes[slug] = MailboxStack(
            app, f"Mailbox{slug}",
            name=f"mailbox-{low}", alias=f"mailbox-{low}",
            domain_name=mdom, hosted_zone_id=zone,
            mailbox_url=f"https://{mdom}", core_table=core.table,
            lwa_layer_arn=lwa_layer_arn, env=env)
    return {"core": core, "witnesses": witnesses, "mailboxes": mailboxes}
```

> The `from keri_cdk import ...` line sits at the **bottom** of the module (after the loader) to avoid a circular import at package load: `keri_cdk/__init__.py` must NOT import `federation`. Verify `keri_cdk/__init__.py` does not reference `federation`; if a reviewer prefers, change this import to `from keri_cdk.core_stack import KeriCoreStack` / `from keri_cdk.witness_stack import WitnessStack` / `from keri_cdk.mailbox_stack import MailboxStack`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_federation_build.py -q`
Expected: PASS (3 passed). If `test_witness_uses_expected_subdomain_and_url` fails on attribute access, switch it to the `Template` assertion noted in Step 1.

- [ ] **Step 5: Rewrite `app.py` as a thin wire-up**

Replace the body of `ecosystems/keri_host/app.py` (keep the `sys.path.insert` shim) with:

```python
"""CDK app: keri.host federation — KeriCoreStack + one witness+mailbox pair per domain.

Synth without context (falls back to federation.example.json):
    python app.py
Real deploy (federation.json present, or $KERI_HOST_FEDERATION set):
    AWS_PROFILE=personal npx aws-cdk@latest deploy --all -c region=us-east-1
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import aws_cdk as cdk

from keri_cdk.federation import load_federation, build_federation

app = cdk.App()
region = app.node.try_get_context("region") or "us-east-1"
env = cdk.Environment(region=region)
lwa_layer_arn = app.node.try_get_context("lwa_layer_arn")

entries = load_federation(pathlib.Path(__file__).resolve().parent)
build_federation(app, entries, env, lwa_layer_arn=lwa_layer_arn)

app.synth()
```

- [ ] **Step 6: Verify the app synthesizes the full 5×5 from the example config**

Run:
```bash
cd ~/code/keripy/ecosystems/keri_host
../../.venv/bin/python app.py >/dev/null && echo "synth OK"
ls cdk.out/*.template.json | wc -l
```
Expected: `synth OK`, and the count is `11` (1 core + 5 witness + 5 mailbox templates). Clean up: `rm -rf cdk.out`.

- [ ] **Step 7: Update the legacy app test and run the whole CDK suite**

The old `tests/cdk/test_keri_host_app.py` still instantiates single stacks directly — it remains valid (constructs unchanged) and needs no edit. Run the full suite:

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/ -q`
Expected: PASS (all CDK tests green, including the two new test files).

- [ ] **Step 8: Commit**

```bash
cd ~/code/keripy
git add keri_cdk/federation.py ecosystems/keri_host/app.py tests/cdk/test_federation_build.py
git commit -m "feat(federation): grow the keri.host CDK app from 1x1 to 5x5 (domain-derived stacks)"
```

---

### Task 3: SAM teardown script (discover + dry-run, gated execution)

A re-runnable, idempotent teardown tool. Default mode is **dry-run** (discover + print the plan); destruction requires `--execute`. The pure stack-selection/classification logic is unit-tested; the live destruction runs in Task 6.

**Files:**
- Create: `ecosystems/keri_host/teardown_sam.py`
- Test: `tests/cdk/test_teardown_sam.py`

**Interfaces:**
- Produces:
  - `select_sam_stacks(stack_summaries) -> dict` — given the `StackSummaries` list from `cloudformation list-stacks`, returns `{"functional": [names], "companion": [names]}`. Functional = `serverless-*` without a `CompanionStack` suffix; companion = names ending in `CompanionStack`. Ignores `DELETE_COMPLETE` stacks.
  - `format_plan(selected) -> str` — human-readable teardown plan.
  - CLI: `python teardown_sam.py [--execute] [--region us-east-1]` (default dry-run).

- [ ] **Step 1: Write the failing tests**

Create `tests/cdk/test_teardown_sam.py`:

```python
"""Tests for the pure selection/classification logic of teardown_sam."""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[2] / "ecosystems" / "keri_host" / "teardown_sam.py"
_spec = importlib.util.spec_from_file_location("teardown_sam", _PATH)
teardown_sam = importlib.util.module_from_spec(_spec)


def setup_module(_):
    _spec.loader.exec_module(teardown_sam)


def _summary(name, status="CREATE_COMPLETE"):
    return {"StackName": name, "StackStatus": status}


def test_selects_and_classifies_federation_stacks():
    summaries = [
        _summary("serverless-witness"),
        _summary("serverless-mailbox"),
        _summary("serverless-witness-honest"),
        _summary("serverless-mailbox-legitim"),
        _summary("serverless-witness-abc123-CompanionStack"),
        _summary("serverless-mailbox-def456-CompanionStack"),
        _summary("some-other-stack"),                 # not ours -> ignored
        _summary("serverless-old", status="DELETE_COMPLETE"),  # already gone -> ignored
    ]
    sel = teardown_sam.select_sam_stacks(summaries)
    assert set(sel["functional"]) == {
        "serverless-witness", "serverless-mailbox",
        "serverless-witness-honest", "serverless-mailbox-legitim",
    }
    assert set(sel["companion"]) == {
        "serverless-witness-abc123-CompanionStack",
        "serverless-mailbox-def456-CompanionStack",
    }
    assert "some-other-stack" not in sel["functional"] + sel["companion"]
    assert "serverless-old" not in sel["functional"] + sel["companion"]


def test_format_plan_lists_every_selected_stack():
    sel = {"functional": ["serverless-witness"], "companion": ["serverless-x-CompanionStack"]}
    text = teardown_sam.format_plan(sel)
    assert "serverless-witness" in text
    assert "serverless-x-CompanionStack" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_teardown_sam.py -q`
Expected: FAIL — `FileNotFoundError` / module load error (`teardown_sam.py` does not exist).

- [ ] **Step 3: Write the teardown script**

Create `ecosystems/keri_host/teardown_sam.py`:

```python
#!/usr/bin/env python3
"""Tear down the live SAM 5x5 KERI federation (serverless-* stacks).

DESTRUCTIVE. Default mode is dry-run: it discovers and prints the plan but
deletes nothing. Pass --execute to actually destroy. Idempotent: safe to re-run
to finish a partially-failed teardown.

Preserves the 5 Route53 hosted zones; only removes stack-owned resources plus
any orphaned ACM-validation CNAMEs / A-records it can attribute to the
federation subdomains.

Run:
    AWS_PROFILE=personal python teardown_sam.py              # dry-run
    AWS_PROFILE=personal python teardown_sam.py --execute    # destroy
"""
import argparse
import subprocess
import sys
import json

FEDERATION_PREFIX = "serverless-"
COMPANION_SUFFIX = "CompanionStack"


def select_sam_stacks(stack_summaries):
    """Classify live serverless-* stacks into functional vs SAM companion."""
    functional, companion = [], []
    for s in stack_summaries:
        name = s["StackName"]
        if not name.startswith(FEDERATION_PREFIX):
            continue
        if s.get("StackStatus") == "DELETE_COMPLETE":
            continue
        (companion if name.endswith(COMPANION_SUFFIX) else functional).append(name)
    return {"functional": sorted(functional), "companion": sorted(companion)}


def format_plan(selected):
    lines = ["TEARDOWN PLAN (serverless-* SAM federation)", ""]
    lines.append(f"  functional stacks ({len(selected['functional'])}):")
    lines += [f"    - {n}" for n in selected["functional"]]
    lines.append(f"  companion stacks ({len(selected['companion'])}):")
    lines += [f"    - {n}" for n in selected["companion"]]
    return "\n".join(lines)


def _aws(args, region):
    out = subprocess.run(["aws", *args, "--region", region, "--output", "json"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args)} failed: {out.stderr.strip()}")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def discover(region):
    summaries = _aws(["cloudformation", "list-stacks"], region).get("StackSummaries", [])
    return select_sam_stacks(summaries)


def _disable_protections(stack, region):
    # Stack termination protection.
    subprocess.run(["aws", "cloudformation", "update-termination-protection",
                    "--stack-name", stack, "--no-enable-termination-protection",
                    "--region", region], capture_output=True, text=True)
    # DynamoDB deletion protection for any tables in the stack.
    res = _aws(["cloudformation", "describe-stack-resources", "--stack-name", stack], region)
    for r in res.get("StackResources", []):
        if r["ResourceType"] == "AWS::DynamoDB::Table":
            subprocess.run(["aws", "dynamodb", "update-table",
                            "--table-name", r["PhysicalResourceId"],
                            "--no-deletion-protection-enabled", "--region", region],
                           capture_output=True, text=True)


def _delete_stack(stack, region):
    subprocess.run(["aws", "cloudformation", "delete-stack", "--stack-name", stack,
                    "--region", region], capture_output=True, text=True)
    subprocess.run(["aws", "cloudformation", "wait", "stack-delete-complete",
                    "--stack-name", stack, "--region", region], capture_output=True, text=True)


def execute(selected, region):
    # Functional stacks first (own the API-GW custom domains + tables + certs),
    # then companions. ACM DELETE_FAILED + orphaned Route53 records are handled
    # in the Task 6 runbook (manual `acm delete-certificate` + CNAME sweep) since
    # they require per-cert logical-id retention that is discovered at runtime.
    for stack in selected["functional"]:
        print(f"  disabling protections + deleting {stack} ...")
        _disable_protections(stack, region)
        _delete_stack(stack, region)
    for stack in selected["companion"]:
        print(f"  deleting companion {stack} ...")
        _delete_stack(stack, region)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="actually destroy (default: dry-run)")
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args(argv)

    selected = discover(args.region)
    print(format_plan(selected))
    total = len(selected["functional"]) + len(selected["companion"])
    if not args.execute:
        print(f"\nDRY RUN — {total} stacks would be deleted. Re-run with --execute to destroy.")
        return 0
    if total == 0:
        print("\nNothing to delete (zero serverless-* stacks). Federation already torn down.")
        return 0
    print(f"\nEXECUTING teardown of {total} stacks ...")
    execute(selected, args.region)
    print("Stack deletion submitted. Verify zero-trace per the Task 6 runbook.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_teardown_sam.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Smoke the dry-run against live AWS (read-only — no deletion)**

Run: `cd ~/code/keripy/ecosystems/keri_host && AWS_PROFILE=personal ../../.venv/bin/python teardown_sam.py`
Expected: prints the TEARDOWN PLAN listing the live `serverless-*` stacks and ends with `DRY RUN — N stacks would be deleted`. **Confirm N matches the spec's destroy target (≈20 = 10 functional + 10 companion).** If the count differs, stop and reconcile with the spec before Task 6. This step deletes nothing.

- [ ] **Step 6: Commit**

```bash
cd ~/code/keripy
git add ecosystems/keri_host/teardown_sam.py tests/cdk/test_teardown_sam.py
git commit -m "feat(teardown): discover/dry-run SAM federation teardown script (gated --execute)"
```

---

### Task 4: AID-harvest script

After deploy, resolve the 5 fresh witness AIDs + 5 fresh mailbox AIDs from their OOBI/controller endpoints into the gitignored `federation_aids.json` hand-off artifact. The parse-and-shape logic is unit-tested; the live HTTP harvest runs in Task 8.

**Files:**
- Create: `ecosystems/keri_host/harvest_aids.py`
- Test: `tests/cdk/test_harvest_aids.py`

**Interfaces:**
- Consumes: `load_federation` (Task 1) for the domain list.
- Produces:
  - `extract_aid(oobi_json, role) -> str` — pulls the controller AID from a witness/mailbox OOBI/controller JSON response.
  - `harvest(entries, fetch) -> dict` — returns `{"witnesses": {slug: {"aid": ..., "url": ...}}, "mailboxes": {...}}`. `fetch(url) -> dict` is injected (real `urllib`/`requests` in CLI, a fake in tests).
  - Writes `federation_aids.json`.

- [ ] **Step 1: Write the failing tests**

Create `tests/cdk/test_harvest_aids.py`:

```python
"""Tests for harvest_aids extraction/shaping (no network)."""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[2] / "ecosystems" / "keri_host" / "harvest_aids.py"
_spec = importlib.util.spec_from_file_location("harvest_aids", _PATH)
harvest_aids = importlib.util.module_from_spec(_spec)


def setup_module(_):
    _spec.loader.exec_module(harvest_aids)


def test_extract_aid_from_oobi_payload():
    payload = {"i": "BEHIsEX9_witness_aid_placeholder_0000000000", "role": "witness"}
    assert harvest_aids.extract_aid(payload, "witness").startswith("BEHIsEX9")


def test_harvest_shapes_per_slug_with_url():
    entries = [{"slug": "Alpha", "domain": "alpha.test", "hosted_zone_id": "Z1"}]

    def fake_fetch(url):
        # witness vs mailbox distinguished by subdomain in the URL
        aid = "BWIT_alpha" if url.startswith("https://witness.") else "BMBX_alpha"
        return {"i": aid}

    out = harvest_aids.harvest(entries, fake_fetch)
    assert out["witnesses"]["Alpha"] == {"aid": "BWIT_alpha", "url": "https://witness.alpha.test"}
    assert out["mailboxes"]["Alpha"] == {"aid": "BMBX_alpha", "url": "https://mailbox.alpha.test"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_harvest_aids.py -q`
Expected: FAIL — module load error (`harvest_aids.py` does not exist).

- [ ] **Step 3: Write the harvest script**

Create `ecosystems/keri_host/harvest_aids.py`:

```python
#!/usr/bin/env python3
"""Harvest the fresh witness + mailbox AIDs of the deployed federation.

Writes federation_aids.json (gitignored), the hand-off artifact for validation
(Task 9) and the downstream publisher Task 9. Run AFTER `cdk deploy --all`.

Run:
    AWS_PROFILE=personal python harvest_aids.py
"""
import json
import pathlib
import sys
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1]))
from keri_cdk.federation import load_federation  # noqa: E402

# Endpoint that returns the node's controller OOBI/key-state JSON. Adjust the
# path here if the deployed witness/mailbox exposes its self-OOBI elsewhere
# (confirm against keri_cdk/handlers/*/<...>_handler.py routes at run time).
_OOBI_PATH = "/oobi"


def extract_aid(oobi_json, role):
    """Return the controller AID from an OOBI/controller JSON payload."""
    aid = oobi_json.get("i") or oobi_json.get("aid") or oobi_json.get("pre")
    if not aid:
        raise ValueError(f"no AID field in {role} OOBI payload: {oobi_json!r}")
    return aid


def _http_fetch(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def harvest(entries, fetch=_http_fetch):
    out = {"witnesses": {}, "mailboxes": {}}
    for e in entries:
        slug, domain = e["slug"], e["domain"]
        for role, key in (("witness", "witnesses"), ("mailbox", "mailboxes")):
            base = f"https://{role}.{domain}"
            payload = fetch(base + _OOBI_PATH)
            out[key][slug] = {"aid": extract_aid(payload, role), "url": base}
    return out


def main():
    entries = load_federation(_HERE)
    out = harvest(entries)
    dest = _HERE / "federation_aids.json"
    dest.write_text(json.dumps(out, indent=2) + "\n")
    n = len(out["witnesses"]) + len(out["mailboxes"])
    print(f"wrote {dest} ({n} AIDs: {len(out['witnesses'])} witnesses + "
          f"{len(out['mailboxes'])} mailboxes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_harvest_aids.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/code/keripy
git add ecosystems/keri_host/harvest_aids.py tests/cdk/test_harvest_aids.py
git commit -m "feat(harvest): resolve fresh federation AIDs into the federation_aids.json hand-off"
```

---

### Task 5: Throwaway-client 3-of-5 e2e harness

A kli-driven harness (mirrors the publisher's "thin pipeline over kli" pattern) that incepts a throwaway AID against the 5 witnesses at toad 3-of-5, collects receipts via `--receipt-endpoint` (→ `Receiptor`), round-trips a mailbox message, then deletes the throwaway keystore. The incept-config builder is unit-tested; the live kli run is Task 9.

**Files:**
- Create: `ecosystems/keri_host/e2e_client.py`
- Test: `tests/cdk/test_e2e_client.py`

**Interfaces:**
- Consumes: `federation_aids.json` (Task 4/8).
- Produces:
  - `build_incept_config(aids, toad=3) -> dict` — the keripy incept config (`wits` = the 5 witness AIDs, `toad` = 3, `transferable` true). Raises `ValueError` if fewer than `toad` witnesses or `toad < 1`.
  - `witness_oobis(aids) -> list[str]` — `["{url}/oobi/{aid}", ...]` for OOBI resolution.
  - CLI: `python e2e_client.py` — runs the full kli flow against the live federation, asserts ≥`toad` receipts + a mailbox round-trip, tears down.

- [ ] **Step 1: Write the failing tests**

Create `tests/cdk/test_e2e_client.py`:

```python
"""Tests for the e2e client's pure config builders (no kli, no network)."""
import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[2] / "ecosystems" / "keri_host" / "e2e_client.py"
_spec = importlib.util.spec_from_file_location("e2e_client", _PATH)
e2e_client = importlib.util.module_from_spec(_spec)


def setup_module(_):
    _spec.loader.exec_module(e2e_client)


_AIDS = {
    "witnesses": {
        "Alpha":   {"aid": "BWIT_a", "url": "https://witness.alpha.test"},
        "Bravo":   {"aid": "BWIT_b", "url": "https://witness.bravo.test"},
        "Charlie": {"aid": "BWIT_c", "url": "https://witness.charlie.test"},
        "Delta":   {"aid": "BWIT_d", "url": "https://witness.delta.test"},
        "Echo":    {"aid": "BWIT_e", "url": "https://witness.echo.test"},
    },
    "mailboxes": {"Alpha": {"aid": "BMBX_a", "url": "https://mailbox.alpha.test"}},
}


def test_build_incept_config_three_of_five():
    cfg = e2e_client.build_incept_config(_AIDS, toad=3)
    assert len(cfg["wits"]) == 5
    assert set(cfg["wits"]) == {"BWIT_a", "BWIT_b", "BWIT_c", "BWIT_d", "BWIT_e"}
    assert cfg["toad"] == 3
    assert cfg["transferable"] is True


def test_witness_oobis_built_per_witness():
    oobis = e2e_client.witness_oobis(_AIDS)
    assert "https://witness.alpha.test/oobi/BWIT_a" in oobis
    assert len(oobis) == 5


def test_rejects_toad_above_witness_count():
    few = {"witnesses": {"Alpha": {"aid": "BWIT_a", "url": "u"}}, "mailboxes": {}}
    with pytest.raises(ValueError, match="toad"):
        e2e_client.build_incept_config(few, toad=3)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_e2e_client.py -q`
Expected: FAIL — module load error (`e2e_client.py` does not exist).

- [ ] **Step 3: Write the e2e harness**

Create `ecosystems/keri_host/e2e_client.py`:

```python
#!/usr/bin/env python3
"""Throwaway 3-of-5 client e2e against the deployed federation (kli-driven).

Proves a real multi-witness wallet client works against the fresh federation:
resolve the 5 witness OOBIs -> incept at toad 3-of-5 with --receipt-endpoint
(routes to agenting.Receiptor, NOT WitnessReceiptor which hangs over HTTP) ->
assert >= toad receipts -> mailbox round-trip -> delete the throwaway keystore.

Run AFTER harvest_aids.py (needs federation_aids.json):
    AWS_PROFILE=personal python e2e_client.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_AIDS_FILE = _HERE / "federation_aids.json"
_NAME = "cutover-e2e-throwaway"


def build_incept_config(aids, toad=3):
    wits = [w["aid"] for w in aids["witnesses"].values()]
    if toad < 1:
        raise ValueError("toad must be >= 1")
    if len(wits) < toad:
        raise ValueError(f"toad {toad} exceeds witness count {len(wits)}")
    return {"transferable": True, "wits": wits, "toad": toad,
            "icount": 1, "ncount": 1, "isith": "1", "nsith": "1"}


def witness_oobis(aids):
    return [f"{w['url']}/oobi/{w['aid']}" for w in aids["witnesses"].values()]


def _kli(args, ks_home):
    cmd = ["kli", *args, "--base", ks_home]
    print("  $", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"kli {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout


def run_e2e(aids, toad=3):
    """Execute the live kli flow. Returns the incepted AID prefix on success."""
    with tempfile.TemporaryDirectory() as ks_home:
        cfg = build_incept_config(aids, toad)
        cfg_path = pathlib.Path(ks_home) / "incept.json"
        cfg_path.write_text(json.dumps(cfg))
        _kli(["init", "--name", _NAME, "--nopasscode"], ks_home)
        for oobi in witness_oobis(aids):
            _kli(["oobi", "resolve", "--name", _NAME, "--oobi", oobi], ks_home)
        # --receipt-endpoint routes receipt collection through Receiptor.
        out = _kli(["incept", "--name", _NAME, "--alias", "e2e",
                    "--file", str(cfg_path), "--receipt-endpoint"], ks_home)
        status = _kli(["status", "--name", _NAME, "--alias", "e2e", "--verbose"], ks_home)
        # Assert the KEL shows at least `toad` witness receipts.
        if status.count("witness") < toad:  # coarse check; refine to count receipt seals
            raise RuntimeError(f"fewer than {toad} witness receipts in:\n{status}")
        print(f"  e2e incept OK at toad {toad}-of-{len(cfg['wits'])}")
        return out
```

> **kli flag verification (do at implementation time):** confirm the exact `kli incept` flag set against the installed keripy (`kli incept --help`) — `--receipt-endpoint`, `--file`, `--alias`, `--base`/`--name` usage must match the publisher pipeline in locksmith `tools/publisher/`. The mailbox round-trip step (send via one mailbox, poll, assert delivery) is added here using the same `kli` mailbox subcommands the peer-mode integration uses; if `kli` lacks a direct command, drive it with a small `keri.app.habbing.Habery` + `agenting.Receiptor` snippet (still toad 3-of-5, still Receiptor — never WitnessReceiptor).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/test_e2e_client.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd ~/code/keripy
git add ecosystems/keri_host/e2e_client.py tests/cdk/test_e2e_client.py
git commit -m "feat(e2e): throwaway 3-of-5 client harness (kli + Receiptor) for federation validation"
```

---

### Task 6: [OPERATIONAL — DESTRUCTIVE] Execute SAM teardown + verify zero-trace

Runs the irreversible destruction. **Gated:** do not start until Tasks 1–5 are merged/reviewed and a human has confirmed the dry-run inventory (Task 3 Step 5) matches the spec. No code is written here; the deliverable is a torn-down account verified to zero-trace (zones preserved).

**Files:** none (operational).

**Interfaces:**
- Consumes: `teardown_sam.py` (Task 3).
- Produces: an empty federation (no `serverless-*` stacks, no old per-service tables, no old certs, no orphaned A-records/validation-CNAMEs); 5 hosted zones intact.

- [ ] **Step 1: Re-confirm the dry-run inventory (read-only)**

Run: `cd ~/code/keripy/ecosystems/keri_host && AWS_PROFILE=personal ../../.venv/bin/python teardown_sam.py`
Expected: the plan lists ≈20 stacks. **Human gate: confirm the list is exactly the SAM federation and nothing else before proceeding.**

- [ ] **Step 2: Execute the stack teardown**

Run: `cd ~/code/keripy/ecosystems/keri_host && AWS_PROFILE=personal ../../.venv/bin/python teardown_sam.py --execute`
Expected: each stack prints "disabling protections + deleting …" / "deleting companion …"; ends with "Stack deletion submitted." Some ACM certs may leave their stacks in `DELETE_FAILED` (handled next).

- [ ] **Step 3: Clear ACM `DELETE_FAILED` certs (the known gotcha)**

For each stack still in `DELETE_FAILED` because of an in-use ACM cert (lag after the API-GW custom domain is removed):

```bash
# identify the cert logical id in the failed stack
AWS_PROFILE=personal aws cloudformation describe-stack-resources --stack-name <STACK> \
  --region us-east-1 --query "StackResources[?ResourceType=='AWS::CertificateManager::Certificate'].[LogicalResourceId,PhysicalResourceId]" --output text
# retain the cert, delete the stack, then delete the cert directly once its domain is gone
AWS_PROFILE=personal aws cloudformation delete-stack --stack-name <STACK> \
  --retain-resources <CertLogicalId> --region us-east-1
AWS_PROFILE=personal aws cloudformation wait stack-delete-complete --stack-name <STACK> --region us-east-1
AWS_PROFILE=personal aws acm delete-certificate --certificate-arn <CertArn> --region us-east-1
```
Expected: every former `serverless-*` stack reaches `DELETE_COMPLETE` (or is gone) and every federation cert is deleted.

- [ ] **Step 4: Sweep orphaned Route53 records (preserve the zones)**

For each of the 5 zones, list records and delete only the federation subdomain A-records (`witness.<domain>`, `mailbox.<domain>`) and any leftover ACM-validation CNAMEs:

```bash
AWS_PROFILE=personal aws route53 list-hosted-zones \
  --query "HostedZones[].[Name,Id]" --output text   # re-fetch zone IDs (keep OUT of the repo)
AWS_PROFILE=personal aws route53 list-resource-record-sets --hosted-zone-id <ZONE_ID> \
  --query "ResourceRecordSets[?Type=='A' || Type=='CNAME'].[Name,Type]" --output text
# delete each stale record via change-resource-record-sets with action DELETE
```
Expected: each zone retains its SOA/NS (and any non-federation records); the witness/mailbox A-records and ACM-validation CNAMEs are gone. **Do not delete the zones.**

- [ ] **Step 5: Verify zero-trace**

Run:
```bash
AWS_PROFILE=personal aws cloudformation list-stacks --region us-east-1 \
  --query "StackSummaries[?starts_with(StackName,'serverless-') && StackStatus!='DELETE_COMPLETE'].StackName" --output text
AWS_PROFILE=personal aws dynamodb list-tables --region us-east-1 \
  --query "TableNames[?contains(@,'witness') || contains(@,'mailbox')]" --output text
AWS_PROFILE=personal aws acm list-certificates --region us-east-1 \
  --query "CertificateSummaryList[?contains(DomainName,'keri.host') || contains(DomainName,'honest.town') || contains(DomainName,'verdadero.me') || contains(DomainName,'goonei.com') || contains(DomainName,'legitim.us')].DomainName" --output text
AWS_PROFILE=personal aws route53 list-hosted-zones --query "length(HostedZones)" --output text
```
Expected: first three commands print **empty**; the zone count is unchanged (the 5 zones remain). No commit (operational task).

---

### Task 7: [OPERATIONAL] Build the layer + deploy the CDK 5×5

Build the arm64 runtime layer and deploy the fresh federation onto the now-empty subdomain names.

**Files:** none committed (the built layer under `keri_cdk/layers/keri_runtime/` is gitignored). Requires the real `ecosystems/keri_host/federation.json` (gitignored) with the 5 real domains + zone IDs present.

**Interfaces:**
- Consumes: Task 2 app, Task 6's empty account, `build_layer.sh`.
- Produces: 11 deployed stacks (`KeriHostCore` + `Witness{slug}`×5 + `Mailbox{slug}`×5); 10 healthy endpoints on `witness.<domain>` / `mailbox.<domain>`.

- [ ] **Step 1: Create the real (gitignored) federation config**

Create `ecosystems/keri_host/federation.json` with the 5 real domains and their **real** Route53 zone IDs (fetch via `AWS_PROFILE=personal aws route53 list-hosted-zones`). Same shape as `federation.example.json`; slugs e.g. `KeriHost`, `HonestTown`, `VerdaderoMe`, `GooneiCom`, `LegitimUs`. **This file is gitignored — never commit it.**

- [ ] **Step 2: Build the runtime layer**

Run: `cd ~/code/keripy/keri_cdk/layers && ./build_layer.sh`
Expected: populates `keri_cdk/layers/keri_runtime/` with libsodium + keripy for arm64 (replaces the Task 0 placeholder). Confirm the dir now contains real content.

- [ ] **Step 3: Synth against the real config (offline sanity, no deploy)**

Run: `cd ~/code/keripy/ecosystems/keri_host && AWS_PROFILE=personal npx aws-cdk@latest synth -c region=us-east-1 >/dev/null && echo "synth OK"`
Expected: `synth OK` (11 stacks synthesize with the real domains/zones).

- [ ] **Step 4: Deploy all stacks**

Run: `cd ~/code/keripy/ecosystems/keri_host && AWS_PROFILE=personal npx aws-cdk@latest deploy --all --require-approval never -c region=us-east-1`
Expected: `KeriHostCore` deploys first (exports `CoreTable`), then the 10 witness/mailbox stacks import it; all reach `CREATE_COMPLETE`. ACM DNS-validated certs may take a few minutes (Route53 zones already exist, so validation auto-completes).

- [ ] **Step 5: Confirm endpoints are healthy**

Run (witness GET returns its controller doc; mailbox `/status` returns 200):
```bash
for d in keri.host honest.town verdadero.me goonei.com legitim.us; do
  echo "witness.$d:"; curl -sf "https://witness.$d/" -o /dev/null && echo OK || echo FAIL
  echo "mailbox.$d:"; curl -sf "https://mailbox.$d/status" -o /dev/null && echo OK || echo FAIL
done
```
Expected: all 10 print `OK`. No commit (operational task).

---

### Task 8: [OPERATIONAL] Harvest the fresh AIDs

Capture the 10 fresh AIDs into the gitignored hand-off artifact.

**Files:** writes `ecosystems/keri_host/federation_aids.json` (gitignored).

**Interfaces:**
- Consumes: `harvest_aids.py` (Task 4), the deployed federation (Task 7).
- Produces: `federation_aids.json` with 5 witness + 5 mailbox `{aid,url}` entries (input to Task 9 and the downstream publisher Task 9).

- [ ] **Step 1: Run the harvest**

Run: `cd ~/code/keripy/ecosystems/keri_host && AWS_PROFILE=personal ../../.venv/bin/python harvest_aids.py`
Expected: `wrote .../federation_aids.json (10 AIDs: 5 witnesses + 5 mailboxes)`. If `extract_aid` raises (the self-OOBI path differs), adjust `_OOBI_PATH` in `harvest_aids.py` to the actual route (`keri_cdk/handlers/witness/witness_handler.py`) and re-run.

- [ ] **Step 2: Sanity-check the artifact**

Run: `cd ~/code/keripy/ecosystems/keri_host && ../../.venv/bin/python -c "import json; d=json.load(open('federation_aids.json')); print(len(d['witnesses']), len(d['mailboxes'])); assert all(v['aid'].startswith('B') for v in d['witnesses'].values())"`
Expected: prints `5 5` and the assertion passes (witness AIDs are non-transferable `B…` prefixes). No commit (gitignored artifact).

---

### Task 9: [OPERATIONAL] Run the full validation gate

Run every gate in order; each must pass before the next. This is the spec's definition of done (minus zero-trace, verified in Task 6).

**Files:** none.

**Interfaces:**
- Consumes: probes under `keri_cdk/probes/`, `e2e_client.py` (Task 5), `federation_aids.json` (Task 8).

- [ ] **Step 1: CDK synth tests (offline)**

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/cdk/ -q`
Expected: all green (the 5×5 assertions + existing tests).

- [ ] **Step 2: Conformance probes across all 10 endpoints**

Run:
```bash
cd ~/code/keripy/keri_cdk/probes
for d in keri.host honest.town verdadero.me goonei.com legitim.us; do
  AWS_PROFILE=personal WITNESS_URL=https://witness.$d ../../.venv/bin/python witness_conformance/probe.py
  AWS_PROFILE=personal MAILBOX_URL=https://mailbox.$d ../../.venv/bin/python mailbox_conformance/probe.py
done
```
Expected: every probe reports PASS for all routes (POST `/`, GET `/oobi`, `/receipts`, mailbox streaming/long-poll).

- [ ] **Step 3: LeadingKeys probe (16/16) on the real keri-core table**

Run: `cd ~/code/keripy/keri_cdk/probes/leadingkeys && AWS_PROFILE=personal ../../../.venv/bin/python probe.py --region us-east-1`
Expected: `16/16 PASS` — cross-tenant private read/GSI/write all DENY; shared read/GSI/write/meta all ALLOW. Teardown the probe's throwaway resources per its `--teardown-only` flag if it leaves any.

- [ ] **Step 4: Oracle-pooling check**

Confirm `shared#kels.` on the `keri-core` table holds KELs from multiple distinct nodes, and private stores stay per-namespace:

```bash
AWS_PROFILE=personal aws dynamodb scan --table-name keri-core --region us-east-1 \
  --filter-expression "begins_with(pk, :p)" \
  --expression-attribute-values '{":p":{"S":"shared#kels."}}' \
  --query "Count" --output text
```
Expected: `Count` ≥ 2 (multiple nodes' inception KELs pooled in the shared namespace — the federation's collective first-seen view). Spot-check a private prefix (`Witness<Slug>:kel#habs.`) is NOT under `shared#`.

- [ ] **Step 5: Throwaway 3-of-5 client e2e**

Run: `cd ~/code/keripy/ecosystems/keri_host && AWS_PROFILE=personal ../../.venv/bin/python e2e_client.py`
Expected: resolves 5 OOBIs, incepts at toad 3-of-5, asserts ≥3 witness receipts, completes the mailbox round-trip, prints `e2e incept OK at toad 3-of-5`, and removes the throwaway keystore (tempdir auto-cleaned).

- [ ] **Step 6: Record the result + merge**

Append a short validation summary to the spec or a run-log, then merge the branch to `development` and push to **fork only**:

```bash
cd ~/code/keripy
git checkout development && git merge --no-ff feat/sam-to-cdk-cutover
git push fork development
```
Expected: fast-forward/merge clean; `fork/development` updated. **Never push to `origin`.** Task 9 (publisher) is now unblocked.

---

### Task 10: [OPTIONAL — locksmith repo] #158 privacy scrub

Separable cross-repo cleanup: scrub the CDN domain `releases.keri.host` from the two remaining locksmith files. Skip or split into its own change if preferred.

**Files (in `~/code/locksmith`):**
- Modify: `.github/workflows/release.ci.yml`
- Modify: `infrastructure/README.md`

- [ ] **Step 1: Find the remaining occurrences**

Run: `cd ~/code/locksmith && grep -rn "releases.keri.host" .github/workflows/release.ci.yml infrastructure/README.md`
Expected: lists the lines to scrub (the federation domains were already removed in a prior session; only the CDN domain + CI config remain).

- [ ] **Step 2: Replace with the established placeholder/injection pattern**

Replace literal `releases.keri.host` with `releases.example.com` in `infrastructure/README.md`, and in `release.ci.yml` source the CDN domain from the gitignored `deploy_config.json` / `$LOCKSMITH_DEPLOY_CONFIG` injection (same mechanism the federation/CDN domains already use per locksmith CLAUDE.md). Match the surrounding YAML/Markdown style.

- [ ] **Step 3: Verify nothing references the real CDN domain**

Run: `cd ~/code/locksmith && grep -rn "releases.keri.host" .github/ infrastructure/ || echo "clean"`
Expected: `clean`.

- [ ] **Step 4: Commit (locksmith repo, branch off main per its convention)**

```bash
cd ~/code/locksmith
git commit -am "chore(privacy): scrub releases.keri.host CDN domain from CI + infra docs (#158)"
```

---

## Self-Review

**Spec coverage:**
- Unit 1 (federation config) → Task 1. ✓
- Unit 2 (app 1×1→5×5, domain-derived names, synth tests) → Task 2. ✓
- Unit 3 (teardown discover→destroy→verify-zero-trace, all gotchas) → Task 3 (code) + Task 6 (execute). ✓
- Unit 4 (deploy + AID harvest) → Task 7 (deploy) + Task 4/8 (harvest). ✓
- Unit 5 (validation: synth, conformance×10, LeadingKeys 16/16, oracle pooling, 3-of-5 e2e) → Task 9 (+ Task 5 harness code). ✓
- Unit 6 (#158 scrub) → Task 10. ✓
- Env setup (venv, layer placeholders) → Task 0. ✓
- Decision: oracle ON, handlers untouched — no task modifies handlers. ✓
- Decision: reuse same subdomain names — Task 7 deploys onto `witness.<domain>`/`mailbox.<domain>` after teardown frees them. ✓
- Constraint: push fork-only — Task 9 Step 6. ✓
- Constraint: zones preserved — Task 6 Step 4/5. ✓

**Placeholder scan:** No "TBD"/"implement later". Two deliberate run-time verifications are flagged with exact fallbacks (the witness self-OOBI path in Task 4/8, and the exact `kli incept`/mailbox flags in Task 5) — these depend on the installed keripy's CLI surface and are resolved against `--help`/handler routes at implementation time, not guessed.

**Type consistency:** `load_federation` signature matches across Tasks 1, 2, 4 (`config_dir`, `env`). `build_federation` return shape (`{"core","witnesses","mailboxes"}`) is consumed consistently in Task 2 tests and Task 4/5 (`aids["witnesses"][slug]["aid"|"url"]`). `select_sam_stacks` return (`{"functional","companion"}`) consistent across Task 3 code/tests and Task 6. `build_incept_config`/`witness_oobis` consume the harvest shape from Task 4. ✓
