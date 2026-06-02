# Lambda Witness OOBI Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployed Lambda KERI witness at `https://witness.keri.host` return spec-compliant CESR OOBI responses so third-party KERI agents can bootstrap trust.

**Architecture:** Mirror the reference `kli witness` bootstrap flow. On cold start, call `hab.makeEndRole(role=controller)` + `hab.makeLocScheme(url=WITNESS_URL)` and parse through Kevery reply routes to persist signed replies in DynamoDB. Rewrite `handle_oobi_get` to assemble the CESR stream via `hab.replyToOobi()` and return it with `Content-Type: application/cesr`, base64-encoded for API Gateway.

**Tech Stack:** Python 3.14, AWS SAM, AWS Lambda (container image), DynamoDB (via `keri.db.dynamodbing`), keripy `habbing.Habery`, `keri.kering.Roles`/`Schemes`, `keri.help.helping.nowIso8601`.

**Reference documents:**
- Design: `docs/superpowers/specs/2026-04-21-lambda-witness-oobi-design.md`
- Roadmap: `docs/superpowers/specs/2026-04-21-lambda-witness-roadmap.md`

**Constraints:**
- Changes limited to `sam-witness/witness_handler.py`, `sam-witness/template.yaml`, `sam-witness/env.json`, and new SAM event fixtures under `sam-witness/events/`.
- No keripy protocol code (`src/keri/**`) should be modified.
- Deploy target: stack `serverless-witness`, region `us-east-1`, profile `personal`.

**Prerequisites (verify before starting):**
- DynamoDB Local running on `http://localhost:8000` (Docker container `dynamodb-local`)
- Docker daemon running (SAM build uses `--use-container`)
- AWS CLI profile `personal` configured with credentials for the target AWS account
- Currently on branch `feat/dynamodb-backend` (contains prior work)

---

## File Structure

All changes are contained in `sam-witness/`:

| File | Purpose | Action |
|------|---------|--------|
| `sam-witness/template.yaml` | SAM infrastructure definition | Modify — add `WITNESS_URL` env var, add `Globals.Api.BinaryMediaTypes` |
| `sam-witness/env.json` | Local-testing env-var overrides | Modify — add `WITNESS_URL` placeholder |
| `sam-witness/witness_handler.py` | Lambda handler | Modify — add URL registration in `init()`, rewrite `handle_oobi_get()` |
| `sam-witness/events/oobi-get.json` | SAM local-invoke event fixture | Create — API Gateway event for `GET /oobi` |

No new Python modules, no changes under `src/keri/`, no test-suite additions (existing `tests/app/test_lambding.py` and `tests/db/test_dynamodbing.py` serve as regression guard).

---

## Task 1: Capture baseline + regression guard

**Files:** No file changes. Verification only.

**Why:** Before touching anything, confirm the existing test suite is green and capture the pre-change OOBI response so the before/after difference is provable.

- [ ] **Step 1: Verify regression-guard tests pass on current code**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -m pytest tests/app/test_lambding.py tests/db/test_dynamodbing.py -q 2>&1 | tail -5
```

Expected (exact):
```
98 passed, 1 warning in ~5s
```

If fewer than 98 pass, **STOP**. Something is broken in the baseline and must be fixed before continuing.

- [ ] **Step 2: Capture current live OOBI response for before/after comparison**

Run:
```bash
curl -sI https://witness.keri.host/oobi > /tmp/oobi-before.headers
curl -s  https://witness.keri.host/oobi > /tmp/oobi-before.body
cat /tmp/oobi-before.headers
echo "---"
cat /tmp/oobi-before.body
```

Expected:
```
HTTP/2 200
content-type: application/json
...
---
{"oobi": "https://witness.keri.host/oobi/B.../witness", "pre": "B...", "role": "witness"}
```

This confirms the current (non-compliant) behavior. Keep `/tmp/oobi-before.*` for later comparison.

- [ ] **Step 3: Confirm baseline witness_handler.py structure**

Run:
```bash
grep -n "^def " sam-witness/witness_handler.py
```

Expected output (functions in this order):
```
17:def _clear_keeper(ks):
31:def init():
122:def handler(event, context):
152:def handle_status():
160:def handle_cesr_ingest(event):
189:def handle_receipt_post(event):
222:def handle_receipt_get(event):
256:def handle_query_get(event):
284:def handle_oobi_get(event):
320:def get_body_bytes(event):
332:def response(status, body):
```

If line numbers drift ±5 that's fine; if any function is missing or extra, **STOP** and reconcile.

- [ ] **Step 4: No commit for this task**

Verification only. Proceed to Task 2.

---

## Task 2: Add BinaryMediaTypes + WITNESS_URL to template.yaml

**Files:**
- Modify: `sam-witness/template.yaml`

**Why:** `Content-Type: application/cesr` must be declared as a binary media type so API Gateway decodes the base64 body Lambda returns. `WITNESS_URL` env var delivers the public URL to the handler at cold-start time.

- [ ] **Step 1: Add Globals.Api.BinaryMediaTypes block**

Edit `sam-witness/template.yaml`. Find the `Globals:` section (starting at line 8):

```yaml
Globals:
  Function:
    Timeout: 120
    MemorySize: 1024
    Architectures:
      - arm64
```

Replace it with:

```yaml
Globals:
  Function:
    Timeout: 120
    MemorySize: 1024
    Architectures:
      - arm64
  Api:
    BinaryMediaTypes:
      - application/cesr
      - "*/*"
```

- [ ] **Step 2: Add WITNESS_URL to Environment.Variables**

Find the `Environment.Variables` block inside `WitnessFunction.Properties`. It currently ends with `LD_LIBRARY_PATH: /var/task/lib`:

```yaml
      Environment:
        Variables:
          WITNESS_NAME: !Ref WitnessName
          WITNESS_ALIAS: !Ref WitnessAlias
          WITNESS_BASER_TABLE: !Ref WitnessBaserTable
          WITNESS_KEEPER_TABLE: !Ref WitnessKeeperTable
          WITNESS_SALT: !Ref WitnessSalt
          WITNESS_REGION: !Ref AWS::Region
          WITNESS_ENDPOINT_URL: ""
          LD_LIBRARY_PATH: /var/task/lib
```

Replace the whole block with:

```yaml
      Environment:
        Variables:
          WITNESS_NAME: !Ref WitnessName
          WITNESS_ALIAS: !Ref WitnessAlias
          WITNESS_BASER_TABLE: !Ref WitnessBaserTable
          WITNESS_KEEPER_TABLE: !Ref WitnessKeeperTable
          WITNESS_SALT: !Ref WitnessSalt
          WITNESS_REGION: !Ref AWS::Region
          WITNESS_ENDPOINT_URL: ""
          WITNESS_URL: !Sub "https://${DomainName}"
          LD_LIBRARY_PATH: /var/task/lib
```

- [ ] **Step 3: Validate template**

Run:
```bash
sam validate --template sam-witness/template.yaml --region us-east-1 --profile personal 2>&1
```

Expected:
```
.../sam-witness/template.yaml is a valid SAM Template
```

If validation fails, fix the YAML and re-run until it passes.

- [ ] **Step 4: Commit**

```bash
git add sam-witness/template.yaml
git commit -m "$(cat <<'EOF'
feat(witness): add BinaryMediaTypes and WITNESS_URL env var

Declares application/cesr as binary so API Gateway decodes base64 Lambda
responses. WITNESS_URL is derived from DomainName and passed to the
handler so the Lambda can register its own /loc/scheme reply during init.

Phase 1 of the OOBI compliance design.
EOF
)"
```

---

## Task 3: Add WITNESS_URL placeholder to env.json

**Files:**
- Modify: `sam-witness/env.json`

**Why:** `sam local invoke` reads `env.json` to override env vars declared in the template. The template's default (empty endpoint + derived URL) needs an explicit placeholder for local testing where no real public URL exists.

- [ ] **Step 1: Add WITNESS_URL field**

Edit `sam-witness/env.json`. Current contents:

```json
{
  "WitnessFunction": {
    "WITNESS_NAME": "witness-test",
    "WITNESS_ALIAS": "witness",
    "WITNESS_ENDPOINT_URL": "http://host.docker.internal:8000",
    "WITNESS_REGION": "us-west-2",
    "WITNESS_SALT": "",
    "AWS_ACCESS_KEY_ID": "fake",
    "AWS_SECRET_ACCESS_KEY": "fake"
  }
}
```

Replace with:

```json
{
  "WitnessFunction": {
    "WITNESS_NAME": "witness-test",
    "WITNESS_ALIAS": "witness",
    "WITNESS_ENDPOINT_URL": "http://host.docker.internal:8000",
    "WITNESS_REGION": "us-west-2",
    "WITNESS_SALT": "",
    "WITNESS_URL": "http://localhost:3000",
    "AWS_ACCESS_KEY_ID": "fake",
    "AWS_SECRET_ACCESS_KEY": "fake"
  }
}
```

- [ ] **Step 2: Validate JSON**

Run:
```bash
python3 -c "import json; json.load(open('sam-witness/env.json'))" && echo "JSON OK"
```

Expected:
```
JSON OK
```

- [ ] **Step 3: Commit**

```bash
git add sam-witness/env.json
git commit -m "feat(witness): add WITNESS_URL placeholder for local SAM testing"
```

---

## Task 4: Add OOBI event fixture

**Files:**
- Create: `sam-witness/events/oobi-get.json`

**Why:** `sam local invoke` needs an event JSON that simulates an API Gateway `GET /oobi` request so we can exercise the rewritten handler without going through real API Gateway.

- [ ] **Step 1: Create the event fixture**

Write file `sam-witness/events/oobi-get.json` with contents:

```json
{
  "body": null,
  "resource": "/oobi",
  "path": "/oobi",
  "httpMethod": "GET",
  "headers": {
    "Host": "localhost:3000"
  },
  "queryStringParameters": null,
  "requestContext": {
    "resourcePath": "/oobi",
    "httpMethod": "GET"
  }
}
```

- [ ] **Step 2: Validate JSON**

Run:
```bash
python3 -c "import json; json.load(open('sam-witness/events/oobi-get.json'))" && echo "JSON OK"
```

Expected:
```
JSON OK
```

- [ ] **Step 3: Commit**

```bash
git add sam-witness/events/oobi-get.json
git commit -m "test(witness): add SAM event fixture for GET /oobi local invocation"
```

---

## Task 5: Add URL registration to `init()`

**Files:**
- Modify: `sam-witness/witness_handler.py` (imports + init body)

**Why:** On every cold start, the witness must call `makeEndRole(role=controller)` and `makeLocScheme(url, scheme)` with its own public URL, then route the resulting reply messages through `psr.parse()` so Kevery stores them in `db.rpys` / `db.scgs` / `db.lans` / `db.ends` / `db.eans`. Without this, `replyToOobi()` returns only the bare KEL and resolvers cannot bind AID↔URL.

- [ ] **Step 1: Add the URL registration block to `init()`**

Open `sam-witness/witness_handler.py`. Find the block at lines 111-117:

```python
    # Get or create witness Hab (non-transferable)
    _hab = _hby.habByName(alias)
    if _hab is None:
        _hab = _hby.makeHab(name=alias, transferable=False, isith='1', icount=1, ncount=0, nsith='0')

    # Set up parser with Kevery for processing incoming events
    _parser = _hby.psr
```

Replace it with:

```python
    # Get or create witness Hab (non-transferable)
    _hab = _hby.habByName(alias)
    if _hab is None:
        _hab = _hby.makeHab(name=alias, transferable=False, isith='1', icount=1, ncount=0, nsith='0')

    # Register witness URL and controller-role authorization so OOBI resolvers
    # get signed /loc/scheme and /end/role/add replies. BADA monotonicity via
    # nowIso8601() stamp makes cold-start re-registration safe (db.*.pin
    # overwrites cleanly on newer timestamps).
    from keri.kering import Roles, Schemes
    from keri.help import helping

    witness_url = os.environ.get("WITNESS_URL", "").strip()
    if witness_url:
        scheme = Schemes.https if witness_url.startswith("https://") else Schemes.http
        stamp = helping.nowIso8601()
        url_msgs = bytearray()
        url_msgs.extend(_hab.makeEndRole(eid=_hab.pre, role=Roles.controller, stamp=stamp))
        url_msgs.extend(_hab.makeLocScheme(url=witness_url, scheme=scheme, stamp=stamp))
        try:
            _hby.psr.parse(ims=url_msgs)
        except Exception as exc:
            logger.warning("Failed to register witness URL %s: %s", witness_url, exc)
    else:
        logger.warning("WITNESS_URL not set; OOBI responses will not include /loc/scheme")

    # Set up parser with Kevery for processing incoming events
    _parser = _hby.psr
```

**Note:** the imports (`Roles, Schemes, helping`) are local to this block rather than module-level so the cost of their module-load only happens on cold start (not per-request), and so they don't interfere with the existing module-level imports.

- [ ] **Step 2: Syntax check**

Run:
```bash
python3 -c "import ast; ast.parse(open('sam-witness/witness_handler.py').read()); print('syntax OK')"
```

Expected:
```
syntax OK
```

If it fails, the traceback will point to the error line — fix and re-run.

- [ ] **Step 3: Commit**

```bash
git add sam-witness/witness_handler.py
git commit -m "$(cat <<'EOF'
feat(witness): register URL via makeEndRole+makeLocScheme at cold start

Replaces empty /loc/scheme database with signed reply messages for the
witness's own AID. Mirrors habbing.py:1200-1217 (reference kli witness
bootstrap). Uses BADA-compliant nowIso8601() stamp so repeated cold
starts overwrite cleanly. Gracefully degrades if WITNESS_URL missing.
EOF
)"
```

---

## Task 6: Verify init() doesn't crash locally

**Files:** None modified (verification only).

**Why:** Confirm the new URL-registration code path runs to completion and the existing status endpoint still works. This is the integration-level "run test, see it pass" for Task 5.

- [ ] **Step 1: Clear DynamoDB Local so init runs from scratch**

Run:
```bash
python3 -c "
import boto3
c = boto3.client('dynamodb', region_name='us-west-2',
                 endpoint_url='http://localhost:8000',
                 aws_access_key_id='fake', aws_secret_access_key='fake')
for t in c.list_tables()['TableNames']:
    c.delete_table(TableName=t)
    print(f'deleted {t}')
print('DynamoDB Local cleared')
"
```

Expected: zero or more "deleted ..." lines followed by "DynamoDB Local cleared". If the script errors with connection refused, **STOP** — DynamoDB Local container isn't running. Start it with `docker start dynamodb-local` and retry.

- [ ] **Step 2: Build the SAM image**

Run:
```bash
sam build --template sam-witness/template.yaml --use-container 2>&1 | tail -3
```

Expected (last 3 lines):
```
[*] Invoke Function: sam local invoke
[*] Test Function in the Cloud: sam sync --stack-name {{stack-name}} --watch
[*] Deploy: sam deploy --guided
```

Build takes 2-5 minutes. If it fails, read the error — likely a syntax error from Task 5 that Python syntax-check didn't catch at import time.

- [ ] **Step 3: Tag image for sam local invoke**

Run:
```bash
docker tag witnessfunction:latest witness-handler:latest
```

- [ ] **Step 4: Invoke GET / (status endpoint) to verify init succeeds**

Run:
```bash
sam local invoke WitnessFunction \
    --template sam-witness/template.yaml \
    --env-vars sam-witness/env.json \
    --event sam-witness/events/status-get.json 2>&1 | tail -5
```

Expected (last line is JSON response):
```
{"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": "{\"witness\": \"B...\", \"alias\": \"witness\", \"sn\": 0, \"kevers\": 2}"}
```

**Critical check:** The response must be 200 and contain a `witness` prefix. If the response is 500 or shows a Python traceback, the URL-registration code in `init()` is crashing — read the traceback, fix the error, rebuild, rerun.

- [ ] **Step 5: Verify URL was stored in DynamoDB Local**

Run:
```bash
python3 <<'PY'
import boto3
c = boto3.client('dynamodb', region_name='us-west-2',
                 endpoint_url='http://localhost:8000',
                 aws_access_key_id='fake', aws_secret_access_key='fake')
# The subdb 'locs.' is in the baser table (witness-test-db)
tables = c.list_tables()['TableNames']
print('tables:', tables)
# Scan for items in the locs. subdb
resp = c.scan(TableName='witness-test-db',
              FilterExpression='begins_with(PK, :pk)',
              ExpressionAttributeValues={':pk': {'S': 'locs.#'}},
              Select='COUNT')
print('locs. items in witness-test-db:', resp['Count'])
PY
```

Expected:
```
tables: ['witness-test-db', 'witness-test-ks']
locs. items in witness-test-db: 1
```

The `1` confirms `_hby.psr.parse()` stored one location-scheme record during init. If it's `0`, parsing silently failed — check for exceptions in the SAM invoke output from Step 4.

- [ ] **Step 6: No commit for this task**

Verification only. Proceed to Task 7.

---

## Task 7: Rewrite `handle_oobi_get()` for CESR response

**Files:**
- Modify: `sam-witness/witness_handler.py` (replace `handle_oobi_get` function)

**Why:** The current implementation returns JSON. We replace it with the CESR-producing handler that mirrors `src/keri/end/ending.py:558-617`, using `hab.replyToOobi()` to assemble the signed reply stream and `base64` encoding for API Gateway binary transport.

- [ ] **Step 1: Replace `handle_oobi_get` entirely**

Open `sam-witness/witness_handler.py`. Find the existing function at approximately lines 284-315:

```python
def handle_oobi_get(event):
    """GET /oobi, /oobi/{aid}, /oobi/{aid}/{role}, /oobi/{aid}/{role}/{eid}"""
    path = event.get("path", "/oobi")
    parts = [p for p in path.split("/") if p and p != "oobi"]

    # /oobi -- return witness OOBI
    if not parts:
        # Determine our own URL from the request
        headers = event.get("headers") or {}
        host = headers.get("Host", headers.get("host", "localhost"))
        scheme = headers.get("X-Forwarded-Proto", "https")
        url = f"{scheme}://{host}"

        return response(200, {
            "oobi": f"{url}/oobi/{_hab.pre}/witness",
            "pre": _hab.pre,
            "role": "witness",
        })

    aid = parts[0] if len(parts) > 0 else None
    role = parts[1] if len(parts) > 1 else None
    eid = parts[2] if len(parts) > 2 else None

    if aid and aid in _hby.kevers:
        kever = _hby.kevers[aid]
        return response(200, {
            "pre": aid,
            "sn": kever.sn,
            "role": role or "controller",
        })

    return response(404, {"error": f"unknown aid: {aid}"})
```

Replace the whole function (lines 284-315) with:

```python
def handle_oobi_get(event):
    """GET /oobi, /oobi/{aid}, /oobi/{aid}/{role}, /oobi/{aid}/{role}/{eid}

    Returns a signed CESR reply stream (KEL + /loc/scheme + /end/role/add)
    mirroring src/keri/end/ending.py:558-617 OOBIEnd.on_get behavior.
    Body is base64-encoded because API Gateway requires isBase64Encoded=true
    for binary Content-Types.
    """
    from keri.kering import Roles

    path = event.get("path", "/oobi")
    parts = [p for p in path.split("/") if p and p != "oobi"]

    # Bare /oobi defaults to self-OOBI (matches OOBIEnd.on_get default)
    aid  = parts[0] if parts else _hab.pre
    role = parts[1] if len(parts) > 1 else None
    eid  = parts[2] if len(parts) > 2 else None

    if aid not in _hby.kevers:
        return response(404, {"error": f"unknown aid: {aid}"})

    kever = _hby.kevers[aid]
    if not _hby.db.fullyWitnessed(kever.serder):
        return response(404, {"error": "not fully witnessed"})

    # We respond only for AIDs we control or are a witness for
    owits = set(kever.wits)
    if aid not in _hby.prefixes and not owits.intersection(_hby.prefixes):
        return response(406, {"error": "not acceptable"})

    eids = [eid] if eid else []
    msgs = _hab.replyToOobi(aid=aid, role=role, eids=eids)
    if not msgs and role is None:
        msgs = _hab.replyToOobi(aid=aid, role=Roles.witness, eids=eids)
        msgs.extend(_hab.replay(aid))

    if not msgs:
        return response(404, {"error": "no oobi content available"})

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/cesr",
            "KERI-AID": aid,
        },
        "body": base64.b64encode(bytes(msgs)).decode("utf-8"),
        "isBase64Encoded": True,
    }
```

- [ ] **Step 2: Syntax check**

Run:
```bash
python3 -c "import ast; ast.parse(open('sam-witness/witness_handler.py').read()); print('syntax OK')"
```

Expected:
```
syntax OK
```

- [ ] **Step 3: Commit**

```bash
git add sam-witness/witness_handler.py
git commit -m "$(cat <<'EOF'
feat(witness): return signed CESR stream from handle_oobi_get

Replaces JSON response with application/cesr binary body containing the
KEL replay, /loc/scheme reply, and /end/role/add reply produced by
hab.replyToOobi(). Mirrors src/keri/end/ending.py:558-617 status code
semantics (200 / 404 / 406). Body is base64-encoded for API Gateway
binary transport with isBase64Encoded=true.

Completes Phase 1 code changes.
EOF
)"
```

---

## Task 8: Verify OOBI returns CESR locally

**Files:** None modified (verification only).

**Why:** Confirm the rewritten handler returns the right content type, header, and binary body locally before we deploy to AWS.

- [ ] **Step 1: Rebuild SAM image**

Run:
```bash
sam build --template sam-witness/template.yaml --use-container 2>&1 | tail -3
docker tag witnessfunction:latest witness-handler:latest
```

Expected: build succeeds (same 3-line tail as Task 6 Step 2).

- [ ] **Step 2: Invoke GET /oobi and capture output**

Run:
```bash
sam local invoke WitnessFunction \
    --template sam-witness/template.yaml \
    --env-vars sam-witness/env.json \
    --event sam-witness/events/oobi-get.json 2>&1 | tail -5 > /tmp/oobi-local.out
cat /tmp/oobi-local.out
```

Expected: the last line is a JSON Lambda response like:
```json
{"statusCode": 200, "headers": {"Content-Type": "application/cesr", "KERI-AID": "B..."}, "body": "<base64 string>", "isBase64Encoded": true}
```

If `statusCode` is 404 with "unknown aid", the witness isn't self-registered — check that the witness hab's prefix appears in `_hby.kevers` (it should, since `makeHab` was called in init).

If `statusCode` is 404 with "not fully witnessed", the non-trans witness's own KEL is failing the witness-threshold check — check `Baser.fullyWitnessed` behavior for `wits=[]`/`toad=0` (should return True).

- [ ] **Step 3: Decode the base64 body and verify it's CESR**

Run:
```bash
python3 <<'PY'
import json, base64
# Extract body from the last line of /tmp/oobi-local.out
with open('/tmp/oobi-local.out') as f:
    last = f.readlines()[-1].strip()
resp = json.loads(last)
assert resp['statusCode'] == 200, f"expected 200, got {resp['statusCode']}: {resp.get('body')}"
assert resp['headers']['Content-Type'] == 'application/cesr', resp['headers']
assert 'KERI-AID' in resp['headers']
assert resp.get('isBase64Encoded') is True
body = base64.b64decode(resp['body'])
print(f"OK: {len(body)} bytes of CESR")
print(f"AID: {resp['headers']['KERI-AID']}")
# CESR messages start with a version string containing "KERI" magic
assert b'KERI' in body[:200], f"body doesn't look like CESR: {body[:80]!r}"
print(f"first 80 bytes: {body[:80]!r}")
PY
```

Expected:
```
OK: 500-2000 bytes of CESR
AID: B...
first 80 bytes: b'{"v":"KERI10JSON...'
```

If any assertion fails, read the error and fix. The body should be non-empty and contain `KERI` somewhere in the first 200 bytes (version string).

- [ ] **Step 4: No commit for this task**

Verification only. Proceed to Task 9.

---

## Task 9: Full regression test

**Files:** None modified.

**Why:** Confirm the handler changes didn't accidentally break anything in the broader keripy test suite. These are the same tests that ran in Task 1 — they should still all pass.

- [ ] **Step 1: Run the pytest suite**

Run:
```bash
python3 -m pytest tests/app/test_lambding.py tests/db/test_dynamodbing.py -q 2>&1 | tail -5
```

Expected:
```
98 passed, 1 warning in ~5s
```

If any fail, **STOP** and investigate. The plan's constraint is "no keripy protocol code changes" — a regression here means something leaked.

- [ ] **Step 2: No commit for this task**

Verification only.

---

## Task 10: Deploy to AWS

**Files:** None modified.

**Why:** Push the committed code to the production Lambda so the live endpoint returns CESR.

- [ ] **Step 1: Build the image locally for deployment**

Run:
```bash
sam build --template sam-witness/template.yaml --use-container 2>&1 | tail -3
```

Expected: build succeeds (same 3-line tail).

- [ ] **Step 2: Deploy**

Run:
```bash
sam deploy \
    --template-file .aws-sam/build/template.yaml \
    --stack-name serverless-witness \
    --region us-east-1 \
    --profile personal \
    --capabilities CAPABILITY_IAM \
    --resolve-image-repos \
    --resolve-s3 \
    --no-confirm-changeset 2>&1 | tail -30
```

Expected:
- CloudFormation changeset shows updates to `WitnessFunction` (env var + image) and `ServerlessRestApi` (BinaryMediaTypes)
- Deployment completes with `Successfully created/updated stack - serverless-witness in us-east-1`
- Outputs block prints `WitnessApiGw`, `WitnessUrl` (should be `https://witness.keri.host`), and table names

If deployment fails with a CloudFormation error, read the Resource status reason — most common cause is ECR push timeout (retry) or IAM permission for `BinaryMediaTypes` attribute (already granted via `CAPABILITY_IAM`).

- [ ] **Step 3: Confirm stack is healthy**

Run:
```bash
aws cloudformation describe-stacks \
    --stack-name serverless-witness \
    --region us-east-1 \
    --profile personal \
    --query 'Stacks[0].StackStatus' --output text
```

Expected:
```
UPDATE_COMPLETE
```

Any other status (e.g., `UPDATE_ROLLBACK_COMPLETE`) means deployment failed — check CloudFormation console for root cause.

- [ ] **Step 4: No commit for this task**

Infrastructure update only; no code change.

---

## Task 11: Verify live OOBI headers

**Files:** None modified.

**Why:** Confirm the live API Gateway returns the correct `Content-Type` and `KERI-AID` headers.

- [ ] **Step 1: curl HEAD request**

Run:
```bash
curl -sI https://witness.keri.host/oobi
```

Expected headers (content-length and date will vary):
```
HTTP/2 200
content-type: application/cesr
keri-aid: B...
content-length: NNN
...
```

**Critical checks:**
- `content-type: application/cesr` (was `application/json` before — see `/tmp/oobi-before.headers` from Task 1)
- `keri-aid: B...` header present (32-char qb64 starting with `B` for non-trans)

If `content-type` is still `application/json` or `text/plain`, API Gateway isn't treating `application/cesr` as binary. Check `BinaryMediaTypes` block landed in the deployed template (Task 2).

If no `keri-aid` header, the rewritten handler isn't setting it — check Task 7 code landed.

- [ ] **Step 2: No commit for this task**

Verification only.

---

## Task 12: Verify live OOBI round-trip (parse the response)

**Files:** None modified.

**Why:** The definitive acceptance test: a fresh local `Habery` must be able to parse the bytes the live witness returns and end up with the witness's AID in its kevers and the witness's URL in `db.locs`. If this works, a third-party KERI agent can bootstrap trust from our OOBI.

- [ ] **Step 1: Capture live CESR body**

Run:
```bash
curl -s https://witness.keri.host/oobi > /tmp/oobi-live.cesr
file /tmp/oobi-live.cesr
ls -l /tmp/oobi-live.cesr
```

Expected:
```
/tmp/oobi-live.cesr: data
-rw-r--r--  1 ...  ...  NNN Apr 21 ... /tmp/oobi-live.cesr
```

Key: `file` reports `data` (binary), not `ASCII text` or `JSON`. Size is non-zero and probably 500-2000 bytes.

If `file` reports `ASCII text` and the content looks like a base64 string, API Gateway isn't decoding — return to Task 2 and confirm `BinaryMediaTypes` block is in the deployed template.

- [ ] **Step 2: Clear DynamoDB Local and prepare fresh tables**

Run:
```bash
python3 -c "
import boto3
c = boto3.client('dynamodb', region_name='us-west-2',
                 endpoint_url='http://localhost:8000',
                 aws_access_key_id='fake', aws_secret_access_key='fake')
for t in c.list_tables()['TableNames']:
    c.delete_table(TableName=t)
print('cleared')
"
```

Expected: `cleared`.

- [ ] **Step 3: Run the parse round-trip Python acceptance test**

Run:
```bash
python3 <<'PY'
import os
os.environ['AWS_ACCESS_KEY_ID'] = 'fake'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'fake'

from keri.db.dynamodbing import DynamoDBer
from keri.app.lambding import BASER_STORES, KEEPER_STORES, setup_baser, setup_keeper
from keri.app.habbing import Habery
from keri.core.signing import Salter

kwa = dict(region='us-west-2', endpoint_url='http://localhost:8000')
db = setup_baser(DynamoDBer.open(name='verify-db', stores=BASER_STORES,
                                 table_name='verify-db', clear=True, **kwa))
ks = setup_keeper(DynamoDBer.open(name='verify-ks', stores=KEEPER_STORES,
                                  table_name='verify-ks', clear=True, **kwa))

hby = Habery(name='verify', temp=False, free=True, db=db, ks=ks, salt=Salter().qb64)

with open('/tmp/oobi-live.cesr', 'rb') as f:
    cesr = f.read()
print(f'parsing {len(cesr)} bytes of CESR...')
hby.psr.parse(ims=bytearray(cesr))

# The witness's AID should now be in our local kevers (in addition to our own)
our_pre = hby.habByName('verify').pre if hby.habByName('verify') else None
witness_pres = [k for k in hby.kevers if k != our_pre]
print(f'our pre: {our_pre}')
print(f'witness pres found: {witness_pres}')
assert len(witness_pres) >= 1, "no witness AID ingested from OOBI"

witness_pre = witness_pres[0]

# The witness's URL should be stored in db.locs
loc = hby.db.locs.get(keys=(witness_pre, 'https'))
print(f'loc record for {witness_pre} https: {loc}')
assert loc is not None, f"no db.locs entry for {witness_pre}"
assert loc.url == 'https://witness.keri.host', f"wrong URL: {loc.url!r}"

print()
print(f'OOBI round-trip OK')
print(f'  witness AID: {witness_pre}')
print(f'  witness URL: {loc.url}')
print(f'  KEL sn:      {hby.kevers[witness_pre].sn}')
PY
```

Expected output (AID will be the witness's actual qb64):
```
parsing 500-2000 bytes of CESR...
our pre: B...
witness pres found: ['B...']
loc record for B... https: LocationRecord(url='https://witness.keri.host')

OOBI round-trip OK
  witness AID: B...
  witness URL: https://witness.keri.host
  KEL sn:      0
```

**This is the final success criterion.** If `loc.url == 'https://witness.keri.host'` prints, the OOBI is spec-compliant and any third-party KERI agent can use our witness.

If the assertion fails with `loc is None`, the `/loc/scheme` reply didn't parse — most likely signature verification failure. Run this diagnostic to count what did make it into the reply database:

```bash
python3 <<'PY'
import os
os.environ['AWS_ACCESS_KEY_ID'] = 'fake'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'fake'

from keri.db.dynamodbing import DynamoDBer
from keri.app.lambding import BASER_STORES, setup_baser

kwa = dict(region='us-west-2', endpoint_url='http://localhost:8000')
db = setup_baser(DynamoDBer.open(name='verify-db', stores=BASER_STORES,
                                 table_name='verify-db', **kwa))

rpy_count = sum(1 for _ in db.rpys.getItemIter())
lan_count = sum(1 for _ in db.lans.getItemIter())
loc_count = sum(1 for _ in db.locs.getItemIter())
print(f'db.rpys entries: {rpy_count}')
print(f'db.lans entries: {lan_count}')
print(f'db.locs entries: {loc_count}')
PY
```

A count of `0` in `rpys` means Kevery rejected the reply (signature or BADA check). Non-zero `rpys` but `0` in `lans`/`locs` means the reply was stored but indexing failed — unlikely with reference code, but worth checking before blaming the witness side.

- [ ] **Step 4: No commit for this task**

Final acceptance test only. Proceed to close-out.

---

## Close-out

- [ ] **Step 1: Push commits to fork**

```bash
git push fork feat/dynamodb-backend
```

Expected: push succeeds.

- [ ] **Step 2: Summarize what shipped**

Confirm the following are true:
- Commits visible on `fork/feat/dynamodb-backend` on GitHub
- `curl -sI https://witness.keri.host/oobi` returns `content-type: application/cesr` and `keri-aid` header
- The parse round-trip script (Task 12 Step 3) passes and prints the witness URL from `db.locs`
- `pytest tests/app/test_lambding.py tests/db/test_dynamodbing.py -q` still shows 98 passed

Phase 1 is complete. The witness at `https://witness.keri.host` is now spec-compliant and can be discovered + verified by any KERI agent via its OOBI URL.

Next: see `docs/superpowers/specs/2026-04-21-lambda-witness-roadmap.md` Phase 2 (receipt generation polish) for the follow-up design.
