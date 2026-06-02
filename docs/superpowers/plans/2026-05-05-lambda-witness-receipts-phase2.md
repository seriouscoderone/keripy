# Lambda Witness Receipts Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `https://witness.keri.host` produce valid receipts for standard KERI controllers (`kli`, `signify-ts`, `keria`) by reading the `CESR-ATTACHMENT` header, polishing receipt logging/validation, and fixing receipt retrieval.

**Architecture:** Two small helpers (`_extract_cesr_stream`, `_drain_receipt_cues`) added to `sam-witness/witness_handler.py`. Three handlers refactored to use them. Lenient about which CESR format clients send (header-based and inline both accepted). No keripy core changes — Phase 1's `setup_baser` extension already attached the Baser methods we need.

**Tech Stack:** Python 3.14, AWS SAM, AWS Lambda (container image), DynamoDB (via `keri.db.dynamodbing`), keripy `Habery`/`Hab`/`Kevery` from existing imports, Python `logging` module already wired at the top of the handler.

**Reference documents:**
- Design: `docs/superpowers/specs/2026-05-05-lambda-witness-receipts-phase2-design.md`
- Roadmap: `docs/superpowers/specs/2026-04-21-lambda-witness-roadmap.md`
- Phase 1 plan (executed reference): `docs/superpowers/plans/2026-04-21-lambda-witness-oobi-phase1.md`

**Constraints:**
- Changes limited to `sam-witness/witness_handler.py`, `sam-witness/test_live.py`, `sam-witness/test_live.sh`.
- No keripy protocol code (`src/keri/**`) modified.
- No `template.yaml` or `env.json` changes.
- Deploy target: stack `serverless-witness`, region `us-east-1`, profile `personal`.

**Prerequisites:**
- DynamoDB Local running on `http://localhost:8000` (Docker container `dynamodb-local`).
- Docker daemon running.
- `kli` on PATH (`pip install keri` or run from source).
- AWS profile `personal` configured.
- Currently on branch `main` (Phase 1 was merged there).

---

## File Structure

| File | Purpose | Action |
|------|---------|--------|
| `sam-witness/witness_handler.py` | Lambda handler | Modify — add 2 helpers, refactor 3 handlers |
| `sam-witness/test_live.py` | Live conformance tests | Modify — remove xfail decorator, add 2 new tests |
| `sam-witness/test_live.sh` | Bash smoke test using `kli` | Modify — promote receipt-count check from `warn` to `fail` |

No new files. No keripy core changes.

---

## Task 1: Capture baseline + regression guard

**Files:** No file changes. Verification only.

**Why:** Confirm we're on a clean main branch with the existing test suite green and the Phase 1 conformance suite passing as expected (7 passed + 1 xfailed).

- [ ] **Step 1: Confirm we're on main and clean**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
git status --short
git branch --show-current
```

Expected output:
```
(empty)
main
```

If there are uncommitted changes or we're on a different branch, **STOP** and reconcile.

- [ ] **Step 2: Run the regression-guard tests**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -m pytest tests/app/test_lambding.py tests/db/test_dynamodbing.py -q 2>&1 | tail -5
```

Expected (last line):
```
98 passed, 1 warning in ~10s
```

If fewer than 98 pass, **STOP** — something is broken in the baseline.

- [ ] **Step 3: Run the live conformance suite — confirm xfail still xfails**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -m pytest sam-witness/test_live.py -v 2>&1 | tail -15
```

Expected:
```
sam-witness/test_live.py::test_status_endpoint_returns_witness_metadata PASSED
sam-witness/test_live.py::test_oobi_returns_signed_cesr_stream PASSED
sam-witness/test_live.py::test_oobi_round_trip_a_fresh_habery_can_resolve PASSED
sam-witness/test_live.py::test_unknown_aid_returns_404 PASSED
sam-witness/test_live.py::test_post_receipts_returns_signed_witness_receipt PASSED
sam-witness/test_live.py::test_post_empty_body_returns_400 PASSED
sam-witness/test_live.py::test_post_garbage_returns_error PASSED
sam-witness/test_live.py::test_post_receipts_kli_format XFAIL (Phase...)
======== 7 passed, 1 xfailed, 1 warning in ~40s ========
```

The `XFAIL` is expected — we're about to make it pass. If it's `XPASS` instead, somebody already implemented the fix; **STOP** and investigate.

- [ ] **Step 4: Verify witness_handler.py structure matches expectations**

Run:
```bash
grep -n "^def " sam-witness/witness_handler.py
```

Expected output (line numbers may drift ±2):
```
17:def _clear_keeper(ks):
31:def init():
149:def handler(event, context):
179:def handle_status():
189:def handle_cesr_ingest(event):
218:def handle_receipt_post(event):
253:def handle_receipt_get(event):
283:def handle_query_get(event):
311:def handle_oobi_get(event):
363:def get_body_bytes(event):
375:def response(status, body):
```

If any function is missing or extra, **STOP** and reconcile.

- [ ] **Step 5: No commit for this task**

Verification only. Proceed to Task 2.

---

## Task 2: Add `_extract_cesr_stream` helper

**Files:**
- Modify: `sam-witness/witness_handler.py` (insert helper above `handle_cesr_ingest`)

**Why:** Standard KERI clients (`kli`, `signify-ts`, `keria`) split CESR requests across the body and a `CESR-ATTACHMENT` header (see `src/keri/app/httping.py:154`). Our current handlers ignore the header and silently fail. This helper builds the full `ims` stream from both, lenient about either being empty.

- [ ] **Step 1: Insert the helper just before `handle_cesr_ingest`**

Open `sam-witness/witness_handler.py`. Find the line:
```python
def handle_cesr_ingest(event):
```
(currently around line 189).

Insert the following block immediately above it (and above the blank line preceding `handle_cesr_ingest`):

```python
def _extract_cesr_stream(event):
    """Build a CESR ims byte stream from a Lambda HTTP event.

    Supports both equivalent client formats:
      - kli/streamCESRRequests: event Serder in body, signatures in
        the CESR-ATTACHMENT header (see keri/app/httping.py:154).
      - Inline: full CESR stream (event + attachments) in body alone
        (used by our pytest fixtures and ad-hoc curl calls).

    API Gateway header keys are case-sensitive in the event dict, so
    we look up the header case-insensitively.
    """
    body = get_body_bytes(event)
    headers = event.get("headers") or {}
    attachment = ""
    for k, v in headers.items():
        if k.lower() == "cesr-attachment" and v:
            attachment = v
            break
    ims = bytearray(body)
    if attachment:
        ims.extend(attachment.encode("utf-8"))
    return ims


```

The two trailing blank lines preserve PEP 8 spacing between top-level functions.

- [ ] **Step 2: Syntax check**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -c "import ast; ast.parse(open('sam-witness/witness_handler.py').read()); print('syntax OK')"
```

Expected:
```
syntax OK
```

- [ ] **Step 3: Commit**

```bash
cd /Users/seriouscoderone/code/keripy
git add sam-witness/witness_handler.py
git commit -m "$(cat <<'EOF'
feat(witness): add _extract_cesr_stream helper for body+header CESR

Reads the HTTP body and the CESR-ATTACHMENT header (case-insensitive)
and concatenates them into a single ims byte stream. Mirrors the
canonical extraction in keri/app/httping.py:parseCesrHttpRequest but
is lenient about either being empty so inline-CESR-in-body callers
(our pytest fixtures, curl probes) keep working.

Helper is added but not yet wired in. Wiring lands in subsequent
handler-refactor commits.
EOF
)"
```

---

## Task 3: Add `_drain_receipt_cues` helper

**Files:**
- Modify: `sam-witness/witness_handler.py` (insert helper above `handle_cesr_ingest`)

**Why:** Centralizes receipt generation with three improvements over the current inline loops: (1) validates `serder.pre` is in our kevers and that we are listed in its `wits` before signing, (2) logs failures via `logger.warning(exc_info=True)` instead of swallowing silently, (3) re-iterates until the cue queue is quiescent so secondary receipt cues are handled.

- [ ] **Step 1: Insert helper after `_extract_cesr_stream`**

Open `sam-witness/witness_handler.py`. Find the closing line of `_extract_cesr_stream` (the `return ims` followed by two blank lines, just above `def handle_cesr_ingest(event):`).

Insert this block in the gap:

```python
def _drain_receipt_cues(hby, hab):
    """Drain Kevery cues, generate witness receipts, return concatenated CESR.

    Re-iterates until the cue queue stays empty across a pass — covers
    the case where a receipt itself produces follow-on cues. Validates
    that we are a witness for each pre before signing; logs and skips
    otherwise. All exceptions are logged with traceback.

    Returns:
        bytearray: concatenated CESR receipts (empty if nothing produced).
    """
    receipts = bytearray()
    while True:
        produced = False
        while hby.kvy.cues:
            cue = hby.kvy.cues.popleft()
            if cue.get("kin") != "receipt":
                continue
            serder = cue.get("serder")
            if serder is None:
                continue
            kever = hby.kevers.get(serder.pre)
            if kever is None:
                logger.warning("receipt cue for unknown pre=%s; skipping",
                               serder.pre)
                continue
            if hab.pre not in kever.wits:
                logger.info("receipt cue for pre=%s; %s not in wits; skipping",
                            serder.pre, hab.pre)
                continue
            try:
                rct = hab.receipt(serder=serder)
                receipts.extend(rct)
                produced = True
            except Exception as exc:
                logger.warning("hab.receipt failed for pre=%s sn=%s: %s",
                               serder.pre, serder.sn, exc, exc_info=True)
        if not produced:
            break
        hby.kvy.processEscrows()
    return receipts


```

- [ ] **Step 2: Syntax check**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -c "import ast; ast.parse(open('sam-witness/witness_handler.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/seriouscoderone/code/keripy
git add sam-witness/witness_handler.py
git commit -m "$(cat <<'EOF'
feat(witness): add _drain_receipt_cues helper

Replaces the silent `except Exception: pass` cue loop with a single
helper that:
  - validates serder.pre is in hby.kevers AND hab.pre is in kever.wits
    before signing (skip with logger.info/warning otherwise),
  - logs hab.receipt failures via logger.warning(exc_info=True) so
    CloudWatch sees the traceback,
  - re-iterates until the cue queue stays empty across a pass so
    follow-on receipt cues from processEscrows are also handled.

Helper is added but not yet wired in. Wiring lands in the handler
refactor commits.
EOF
)"
```

---

## Task 4: Refactor `handle_cesr_ingest` to use helpers

**Files:**
- Modify: `sam-witness/witness_handler.py` (replace `handle_cesr_ingest`)

**Why:** Switch to the helpers (so `CESR-ATTACHMENT` is honored, exceptions are logged, AID is validated, cues re-iterate). Also persist generated receipts back through `psr.parse` so the witness's own DB sees them — without this, the witness ships receipts but `db.wigs` never gets populated for them.

Behavioral change: response becomes `204 (no body)` instead of `200 + {"processed": True, "receipts_generated": N}`. Matches the reference `OOBIEnd`/witness pattern. The current JSON payload has no consumers we control.

- [ ] **Step 1: Replace `handle_cesr_ingest` entirely**

Open `sam-witness/witness_handler.py`. Find the existing function (currently at line ~189):

```python
def handle_cesr_ingest(event):
    """POST / or PUT / -- ingest raw CESR message."""
    body = get_body_bytes(event)
    if not body:
        return response(400, {"error": "empty body"})

    ims = bytearray(body)
    _hby.psr.parse(ims=ims)
    _hby.kvy.processEscrows()

    # Drain cues and auto-generate receipts for any new events
    receipts = []
    while _hby.kvy.cues:
        cue = _hby.kvy.cues.popleft()
        if cue.get("kin") == "receipt":
            serder = cue.get("serder")
            if serder is not None:
                try:
                    rct = _hab.receipt(serder=serder)
                    receipts.append(bytes(rct))
                except Exception:
                    pass  # skip if can't receipt (not our AID, etc.)

    return response(200, {
        "processed": True,
        "receipts_generated": len(receipts),
    })
```

Replace it with:

```python
def handle_cesr_ingest(event):
    """POST / -- ingest CESR.

    Generates receipts internally (persisted to our own db.wigs/rcts via
    re-parse) but does not return them in the response body. Matches the
    behavior of the reference witness handler at indirecting.py:880.

    Use POST /receipts (handle_receipt_post) for the synchronous
    receipt-back flow that kli's --receipt-endpoint expects.
    """
    ims = _extract_cesr_stream(event)
    if not ims:
        return response(400, {"error": "empty body"})
    _hby.psr.parse(ims=ims)
    _hby.kvy.processEscrows()
    receipts = _drain_receipt_cues(_hby, _hab)
    if receipts:
        # Re-parse so Kevery routes the witness's own receipts into our
        # db.wigs / db.rcts, where handle_receipt_get can find them later.
        _hby.psr.parse(ims=bytearray(receipts))
    return response(204, None)
```

- [ ] **Step 2: Syntax check**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -c "import ast; ast.parse(open('sam-witness/witness_handler.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/seriouscoderone/code/keripy
git add sam-witness/witness_handler.py
git commit -m "$(cat <<'EOF'
feat(witness): refactor handle_cesr_ingest to use helpers

Swap the inline body-only parse + silent cue loop for the new
_extract_cesr_stream and _drain_receipt_cues helpers. The handler
now:

  - reads CESR from body + CESR-ATTACHMENT header (kli/signify/keria
    format) instead of body alone,
  - validates each receipt cue against hby.kevers + kever.wits and
    logs failures via logger.warning(exc_info=True),
  - re-parses generated receipts so the witness's own db.wigs sees
    them (otherwise the witness ships receipts it cannot itself
    return on later GET /receipts queries),
  - returns 204 with empty body on success (was 200 + JSON metadata).
    Matches reference witness behavior at indirecting.py:880.
EOF
)"
```

---

## Task 5: Refactor `handle_receipt_post` to use helpers

**Files:**
- Modify: `sam-witness/witness_handler.py` (replace `handle_receipt_post`)

**Why:** Same refactor as Task 4, but this endpoint returns the receipts in the response body (still as base64 CESR with `Content-Type: application/cesr`). This is the path `kli incept --receipt-endpoint` and `Receiptor.receipt` use to get receipts back synchronously.

- [ ] **Step 1: Replace `handle_receipt_post` entirely**

Open `sam-witness/witness_handler.py`. Find the existing function (currently at line ~218):

```python
def handle_receipt_post(event):
    """POST /receipts -- receive CESR event, return receipt."""
    body = get_body_bytes(event)
    if not body:
        return response(400, {"error": "empty body"})

    # Parse the event
    ims = bytearray(body)
    _hby.psr.parse(ims=ims)
    _hby.kvy.processEscrows()

    # Generate receipts for processed events
    receipt_msgs = bytearray()
    while _hby.kvy.cues:
        cue = _hby.kvy.cues.popleft()
        if cue.get("kin") == "receipt":
            serder = cue.get("serder")
            if serder is not None:
                try:
                    rct = _hab.receipt(serder=serder)
                    receipt_msgs.extend(rct)
                except Exception:
                    pass

    if receipt_msgs:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/cesr"},
            "body": base64.b64encode(bytes(receipt_msgs)).decode("utf-8"),
            "isBase64Encoded": True,
        }

    return response(204, None)
```

Replace it with:

```python
def handle_receipt_post(event):
    """POST /receipts -- ingest event, return signed witness receipt as CESR.

    Synchronous receipt-back flow used by kli incept --receipt-endpoint
    and any agent calling streamCESRRequests with path='/receipts'.
    Body+CESR-ATTACHMENT header format from real KERI clients is
    accepted (and inline-body-only also works for backward compat).
    """
    ims = _extract_cesr_stream(event)
    if not ims:
        return response(400, {"error": "empty body"})
    _hby.psr.parse(ims=ims)
    _hby.kvy.processEscrows()
    receipts = _drain_receipt_cues(_hby, _hab)
    if not receipts:
        return response(204, None)
    # Re-parse so Kevery routes the witness's own receipts into our
    # db.wigs / db.rcts, where handle_receipt_get can find them later.
    _hby.psr.parse(ims=bytearray(receipts))
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/cesr"},
        "body": base64.b64encode(bytes(receipts)).decode("utf-8"),
        "isBase64Encoded": True,
    }
```

- [ ] **Step 2: Syntax check**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -c "import ast; ast.parse(open('sam-witness/witness_handler.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/seriouscoderone/code/keripy
git add sam-witness/witness_handler.py
git commit -m "$(cat <<'EOF'
feat(witness): refactor handle_receipt_post to use helpers

Same body+header CESR support, validation, logging, and cue
re-iteration as handle_cesr_ingest. Adds the local re-parse so
generated receipts land in our db.wigs for later GET /receipts.

Behavior: returns 200 + base64(CESR) when receipts produced,
204 (no body) when not. This is what kli incept --receipt-endpoint
and Receiptor.receipt expect.
EOF
)"
```

---

## Task 6: Fix `handle_receipt_get` to read `db.wigs`

**Files:**
- Modify: `sam-witness/witness_handler.py` (replace `handle_receipt_get`)

**Why:** Witness receipts on a controller event are stored in `db.wigs` (witness signatures, indexed by event dgkey), per `eventing.py:4196-4202`. The current handler reads `db.rcts` which only stores trans-receipter receipts of an AID's events. So `GET /receipts` returns 404 for receipts that exist. Fix: read `db.wigs`.

Also enrich the response with the witness's own AID so callers can verify the wig signature without an extra OOBI round-trip.

- [ ] **Step 1: Replace `handle_receipt_get` entirely**

Open `sam-witness/witness_handler.py`. Find the existing function (currently at line ~253):

```python
def handle_receipt_get(event):
    """GET /receipts?pre=...&sn=... -- fetch stored receipt."""
    params = event.get("queryStringParameters") or {}
    pre = params.get("pre", "")
    sn = int(params.get("sn", "0"))

    if not pre:
        return response(400, {"error": "pre parameter required"})

    from keri.db import dgKey

    # Look up event digest at this sn
    dig = _hby.db.kels.getLast(keys=pre, on=sn)
    if dig is None:
        return response(404, {"error": f"no event at pre={pre} sn={sn}"})

    dig = dig.encode("utf-8") if hasattr(dig, "encode") else dig

    # Look up receipts
    rcts = _hby.db.rcts.get(keys=dgKey(pre=pre.encode("utf-8") if isinstance(pre, str) else pre, dig=dig))
    if not rcts:
        return response(404, {"error": "no receipts found"})

    return response(200, {
        "pre": pre,
        "sn": sn,
        "receipts": len(rcts),
    })
```

Replace it with:

```python
def handle_receipt_get(event):
    """GET /receipts?pre=...&sn=... -- return witness receipts for pre at sn.

    Witness receipts (this witness's signatures) live in db.wigs, not
    db.rcts. Kevery routes non-trans receipt couples to db.wigs when the
    receiptor is in the AID's wits list (see core/eventing.py:4196-4202).
    db.rcts holds trans-receipter receipts only.
    """
    params = event.get("queryStringParameters") or {}
    pre = params.get("pre", "")
    if not pre:
        return response(400, {"error": "pre parameter required"})
    sn = int(params.get("sn", "0"))

    dig = _hby.db.kels.getLast(keys=pre, on=sn)
    if dig is None:
        return response(404, {"error": f"no event at pre={pre} sn={sn}"})
    dig = dig.encode("utf-8") if isinstance(dig, str) else dig

    pre_b = pre.encode("utf-8") if isinstance(pre, str) else pre
    wigs = _hby.db.wigs.get(keys=(pre_b, dig))
    if not wigs:
        return response(404, {"error": "no witness receipts found"})

    return response(200, {
        "pre": pre,
        "sn": sn,
        "witness_receipts": len(wigs),
        "witness_aid": _hab.pre,
    })
```

- [ ] **Step 2: Syntax check**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -c "import ast; ast.parse(open('sam-witness/witness_handler.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
cd /Users/seriouscoderone/code/keripy
git add sam-witness/witness_handler.py
git commit -m "$(cat <<'EOF'
fix(witness): handle_receipt_get reads db.wigs instead of db.rcts

Witness receipts on a controller event are stored in db.wigs, not
db.rcts. Kevery's processNonTransReceipts routes the cigar+verfer
to db.wigs when the receiptor's prefix is in the event's wits list
(eventing.py:4196-4202); db.rcts only holds trans-receipter receipts.

The previous code looked in db.rcts and returned 404 for receipts
that did exist. Response also now includes witness_aid so callers
can verify the receipt signature without an extra OOBI hop.
EOF
)"
```

---

## Task 7: Run regression locally

**Files:** No file changes. Verification only.

**Why:** Confirm the helper additions and handler refactors didn't break anything in the broader keripy test suite (the constraint is "no keripy core changes", so all 98 should still pass).

- [ ] **Step 1: Run pytest**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -m pytest tests/app/test_lambding.py tests/db/test_dynamodbing.py -q 2>&1 | tail -5
```

Expected:
```
98 passed, 1 warning in ~10s
```

If any test fails, **STOP** and investigate. The Phase 2 changes are limited to `sam-witness/witness_handler.py` so a regression here means something leaked.

- [ ] **Step 2: No commit**

Verification only.

---

## Task 8: Build SAM image + smoke test (Phase 1 OOBI must still work)

**Files:** No file changes. Verification only.

**Why:** Confirm the Lambda container still builds and the existing OOBI flow is intact. We are not yet exercising the Phase 2 receipt path locally — it requires a multi-step setup with a fresh DynamoDB Local and the kli format. The live test post-deploy in Task 13 is more authoritative for Phase 2 behavior.

- [ ] **Step 1: Clear DynamoDB Local**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -c "
import boto3
c = boto3.client('dynamodb', region_name='us-west-2',
                 endpoint_url='http://localhost:8000',
                 aws_access_key_id='fake', aws_secret_access_key='fake')
for t in c.list_tables()['TableNames']:
    c.delete_table(TableName=t)
    print(f'deleted {t}')
print('cleared')
"
```

Expected: zero or more "deleted ..." lines, then "cleared". If the script errors with connection refused, **STOP** — DynamoDB Local container isn't running. Start it with `docker start dynamodb-local`.

- [ ] **Step 2: Build the SAM image**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
sam build --template sam-witness/template.yaml --use-container 2>&1 | tail -3
```

Expected (last line should be `[*] Deploy: sam deploy --guided` or similar success indicator). If the build fails, read the error — likely a syntax slip in one of the Phase 2 edits.

- [ ] **Step 3: Tag image for sam local invoke**

Run:
```bash
docker tag witnessfunction:latest witness-handler:latest
```

- [ ] **Step 4: Smoke-test OOBI endpoint locally**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
sam local invoke WitnessFunction \
    --template sam-witness/template.yaml \
    --env-vars sam-witness/env.json \
    --event sam-witness/events/oobi-get.json 2>&1 | tail -3
```

Expected (last line is a JSON Lambda response):
```
{"statusCode": 200, "headers": {"Content-Type": "application/cesr", "KERI-AID": "B..."}, "body": "<base64>", "isBase64Encoded": true}
```

If `statusCode` is anything other than 200, the Phase 2 refactor regressed Phase 1. **STOP** and investigate.

- [ ] **Step 5: Smoke-test status endpoint locally**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
sam local invoke WitnessFunction \
    --template sam-witness/template.yaml \
    --env-vars sam-witness/env.json \
    --event sam-witness/events/status-get.json 2>&1 | tail -3
```

Expected:
```
{"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": "{\"witness\": \"B...\", \"alias\": \"witness\", \"sn\": 0, \"kevers\": 2}"}
```

- [ ] **Step 6: No commit**

Verification only.

---

## Task 9: Deploy to AWS

**Files:** No file changes. Deploy only.

**Why:** Push the committed handler refactor to the live witness so we can run the conformance suite against it.

- [ ] **Step 1: Deploy**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
sam deploy \
    --template-file .aws-sam/build/template.yaml \
    --stack-name serverless-witness \
    --region us-east-1 \
    --profile personal \
    --capabilities CAPABILITY_IAM \
    --resolve-image-repos \
    --resolve-s3 \
    --no-confirm-changeset 2>&1 | tail -25
```

Expected: deployment completes with `Successfully created/updated stack - serverless-witness in us-east-1` and prints the outputs block (WitnessApiGw, WitnessUrl, etc.).

If deployment fails with a CloudFormation error, read the Resource status reason. ECR push timeouts are common — retry once.

- [ ] **Step 2: Confirm stack status**

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

Any other status (e.g. `UPDATE_ROLLBACK_COMPLETE`) means deployment failed — check CloudFormation console.

- [ ] **Step 3: No commit**

Infrastructure update only; no code change.

---

## Task 10: Verify Phase 1 OOBI behavior is intact post-deploy

**Files:** No file changes. Verification only.

**Why:** Before adding Phase 2-specific tests, confirm none of the Phase 1 test_live.py tests regressed (and the xfail still xfails — for now).

- [ ] **Step 1: Run existing test_live.py against live witness**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -m pytest sam-witness/test_live.py -v 2>&1 | tail -20
```

Expected: 7 of the 8 tests pass and `test_post_receipts_kli_format` reports a **FAILURE** (not XPASSED). This is correct and intended: the xfail decorator was created with `strict=True`, which turns an unexpected pass into a hard fail. The pytest line will look like:

```
sam-witness/test_live.py::test_post_receipts_kli_format FAILED
========== 1 failed, 7 passed, 1 warning in ~40s ==========
```

with the failure reason `[XPASS(strict)] Phase 2: handle_cesr_ingest / handle_receipt_post must concatenate CESR-ATTACHMENT...`. That message confirms the implementation works against live — the test now passes, and the strict-xfail turned that pass into a failure to force the decorator's removal.

If any of the 7 originally-passing tests fails, the Phase 2 refactor regressed Phase 1 in a way the local SAM smoke didn't catch. **STOP** and investigate.

If `test_post_receipts_kli_format` reports `XFAILED` instead of `FAILED [XPASS(strict)]`, the deploy did not pick up the new handler code — re-check Task 9 (deploy) and confirm the build came from the latest commit.

- [ ] **Step 2: No commit**

Verification only.

---

## Task 11: Remove `xfail` decorator from kli-format test

**Files:**
- Modify: `sam-witness/test_live.py` (delete the decorator)

**Why:** The implementation now satisfies the test; the strict xfail will fail because the test xpasses. Remove the decorator so it counts as a regular pass.

- [ ] **Step 1: Delete the `@pytest.mark.xfail(...)` decorator above `test_post_receipts_kli_format`**

Open `sam-witness/test_live.py`. Find the block:

```python
@pytest.mark.xfail(
    reason="Phase 2: handle_cesr_ingest / handle_receipt_post must concatenate "
           "CESR-ATTACHMENT header with body. Standard kli/signify/KERIA "
           "clients use streamCESRRequests which puts the event Serder in "
           "the body and signatures in the CESR-ATTACHMENT header. Our "
           "handlers currently only read the body, so without attachments "
           "the witness escrows the event and returns 204 (no receipt). "
           "Roadmap: docs/superpowers/specs/2026-04-21-lambda-witness-roadmap.md",
    strict=True,
)
def test_post_receipts_kli_format(fresh_hby, witness_pre):
```

Delete the `@pytest.mark.xfail(...)` decorator (the whole block including the closing `)`). The function definition `def test_post_receipts_kli_format(fresh_hby, witness_pre):` should remain.

After the edit, the test should look like:

```python
def test_post_receipts_kli_format(fresh_hby, witness_pre):
    """A controller using streamCESRRequests format gets a valid receipt back.
    ...
    """
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    ...
```

- [ ] **Step 2: Run the test specifically**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -m pytest sam-witness/test_live.py::test_post_receipts_kli_format -v 2>&1 | tail -10
```

Expected:
```
sam-witness/test_live.py::test_post_receipts_kli_format PASSED
======== 1 passed, 1 warning in ~5s ========
```

- [ ] **Step 3: Commit**

```bash
cd /Users/seriouscoderone/code/keripy
git add sam-witness/test_live.py
git commit -m "$(cat <<'EOF'
test(witness): un-xfail test_post_receipts_kli_format after Phase 2

Phase 2 implementation now reads the CESR-ATTACHMENT header (the
standard streamCESRRequests format) in addition to inline-CESR-in-body.
The previously-xfail test exercises this format end-to-end against
the deployed witness; remove the decorator so it counts as a regular
PASSED in CI runs.
EOF
)"
```

---

## Task 12: Add `test_get_receipts_after_post` to test_live.py

**Files:**
- Modify: `sam-witness/test_live.py` (append new test)

**Why:** Verify fix #4 (handle_receipt_get reads db.wigs). After POSTing a controller's inception, GET /receipts must return the stored witness signatures with `witness_receipts >= 1` and `witness_aid` set to the witness's pre.

- [ ] **Step 1: Append the new test at the end of `sam-witness/test_live.py`**

Add this function at the end of the file (after the `test_post_receipts_kli_format` function):

```python
def test_get_receipts_after_post(fresh_hby, witness_pre):
    """After POSTing a controller's inception (kli format), GET /receipts
    returns the stored witness signatures.

    Verifies Phase 2 fix #4: handle_receipt_get must read db.wigs (where
    Kevery actually stores witness receipts) instead of db.rcts.
    """
    # Resolve witness OOBI so the local Habery knows the witness's keys.
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    # Build a fresh controller bob with the witness in his wits list.
    bob = fresh_hby.makeHab(
        name="bob", transferable=True,
        isith="1", icount=1, ncount=1, nsith="1",
        toad=1, wits=[witness_pre],
    )

    # POST bob's inception in kli/streamCESRRequests format.
    body, attachment = split_event_and_attachment(bob.makeOwnInception())
    req = urllib.request.Request(
        f"{WITNESS_URL}/receipts",
        data=body,
        headers={
            "Content-Type": "application/cesr",
            "Accept": "application/cesr",
            "CESR-ATTACHMENT": attachment.decode("utf-8"),
            "CESR-DESTINATION": witness_pre,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        assert r.status == 200, f"expected 200 from /receipts, got {r.status}"

    # GET /receipts?pre=...&sn=0 should now return witness_receipts >= 1.
    status, _, body_b = http_get(
        f"/receipts?pre={bob.pre}&sn=0", accept="application/json"
    )
    assert status == 200, f"expected 200 from GET /receipts, got {status}"
    data = json.loads(body_b)
    assert data["pre"] == bob.pre, data
    assert data["sn"] == 0, data
    assert data["witness_receipts"] >= 1, (
        f"expected >=1 witness receipt, got {data['witness_receipts']}"
    )
    assert data["witness_aid"] == witness_pre, data
```

- [ ] **Step 2: Run the new test**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -m pytest sam-witness/test_live.py::test_get_receipts_after_post -v 2>&1 | tail -10
```

Expected:
```
sam-witness/test_live.py::test_get_receipts_after_post PASSED
======== 1 passed, 1 warning in ~10s ========
```

- [ ] **Step 3: Commit**

```bash
cd /Users/seriouscoderone/code/keripy
git add sam-witness/test_live.py
git commit -m "test(witness): add test_get_receipts_after_post for db.wigs read"
```

---

## Task 13: Add `test_post_does_not_receipt_unrelated_aid` to test_live.py

**Files:**
- Modify: `sam-witness/test_live.py` (append new test)

**Why:** Verify fix #3 (AID validation). When a controller incepts WITHOUT listing the witness in its wits, the witness must NOT sign a receipt for it. The receipt cue is filtered by `_drain_receipt_cues`'s `if hab.pre not in kever.wits: skip` branch.

- [ ] **Step 1: Append the new test at the end of `sam-witness/test_live.py`**

Add this function at the end of the file:

```python
def test_post_does_not_receipt_unrelated_aid(fresh_hby, witness_pre):
    """An inception that does not list the witness in wits should not
    get a witness receipt back.

    Verifies Phase 2 fix #3: _drain_receipt_cues skips cues whose pre's
    kever does not include hab.pre in its wits list.
    """
    # Resolve OOBI so the witness's KEL is in our local kevers (otherwise
    # the OOBI parse below would also skip).
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    # bob has NO witnesses (no wits, toad=0). The witness must refuse to
    # receipt his inception — its pre is not in bob.kever.wits = [].
    bob = fresh_hby.makeHab(
        name="bob", transferable=True,
        isith="1", icount=1, ncount=1, nsith="1",
    )
    assert bob.kever.wits == [], (
        f"test setup error: bob should have no wits, got {bob.kever.wits}"
    )

    status, _, body = http_post_cesr("/receipts", bob.makeOwnInception())
    # Either 204 (no receipt produced) is the expected success case; if
    # the witness does return a 200 it must NOT contain a receipt.
    assert status in (200, 204), f"expected 200 or 204, got {status}"
    assert b'"t":"rct"' not in body, (
        f"witness signed for an AID that does not list it as witness: "
        f"{body[:80]!r}"
    )
```

- [ ] **Step 2: Run the new test**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -m pytest sam-witness/test_live.py::test_post_does_not_receipt_unrelated_aid -v 2>&1 | tail -10
```

Expected:
```
sam-witness/test_live.py::test_post_does_not_receipt_unrelated_aid PASSED
======== 1 passed, 1 warning in ~10s ========
```

- [ ] **Step 3: Commit**

```bash
cd /Users/seriouscoderone/code/keripy
git add sam-witness/test_live.py
git commit -m "test(witness): add test_post_does_not_receipt_unrelated_aid"
```

---

## Task 14: Run full live conformance suite

**Files:** No file changes. Verification only.

**Why:** Confirm all 9 tests pass (7 original + 2 new) against the deployed witness. This is the Phase 2 acceptance gate at the test level.

- [ ] **Step 1: Run pytest**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
python3 -m pytest sam-witness/test_live.py -v 2>&1 | tail -15
```

Expected:
```
sam-witness/test_live.py::test_status_endpoint_returns_witness_metadata PASSED
sam-witness/test_live.py::test_oobi_returns_signed_cesr_stream PASSED
sam-witness/test_live.py::test_oobi_round_trip_a_fresh_habery_can_resolve PASSED
sam-witness/test_live.py::test_unknown_aid_returns_404 PASSED
sam-witness/test_live.py::test_post_receipts_returns_signed_witness_receipt PASSED
sam-witness/test_live.py::test_post_empty_body_returns_400 PASSED
sam-witness/test_live.py::test_post_garbage_returns_error PASSED
sam-witness/test_live.py::test_post_receipts_kli_format PASSED
sam-witness/test_live.py::test_get_receipts_after_post PASSED
sam-witness/test_live.py::test_post_does_not_receipt_unrelated_aid PASSED
======== 10 passed, 1 warning in ~50s ========
```

If any of the 10 fail, **STOP** and investigate the specific test before continuing.

- [ ] **Step 2: No commit**

Verification only.

---

## Task 15: Update test_live.sh — promote receipt-count check to fail

**Files:**
- Modify: `sam-witness/test_live.sh`

**Why:** With Phase 2 deployed, `kli incept --receipt-endpoint` against the live witness must produce a stored receipt. Currently the script tolerates `Receipts: 0` with a `warn`. Promote that to a hard `fail` so kli regressions are caught.

- [ ] **Step 1: Replace the warn block with a fail block**

Open `sam-witness/test_live.sh`. Find this block:

```bash
# Witness limitation discovered during Phase 1 conformance testing:
# our handler only reads the HTTP body, but kli (and signify-ts, keria)
# use streamCESRRequests which puts the event in the body and signatures
# in the CESR-ATTACHMENT header. The witness sees an unsigned event,
# escrows it, returns 204. kli reports `Receipts: 0` as a result.
# Phase 2 of the roadmap will fix this by concatenating the header before
# parsing. Until then we verify the witness side directly in step 5.
RECEIPT_LINE=$(kli status --name "$ALICE_NAME" --alias "$ALICE_ALIAS" --verbose 2>&1 \
    | grep -E "^Receipts:" || true)
if echo "$RECEIPT_LINE" | grep -qE "[Rr]eceipts:[[:space:]]+0\b"; then
    warn "kli reports 0 receipts (Phase 2 gap: witness ignores CESR-ATTACHMENT header)"
fi
```

Replace it with:

```bash
# Phase 2 wires the CESR-ATTACHMENT header through to the parser, so kli
# incept --receipt-endpoint must now produce a stored witness receipt.
# Anything other than "Receipts: N" with N >= 1 is a regression.
RECEIPT_LINE=$(kli status --name "$ALICE_NAME" --alias "$ALICE_ALIAS" --verbose 2>&1 \
    | grep -E "^Receipts:" || true)
if echo "$RECEIPT_LINE" | grep -qE "[Rr]eceipts:[[:space:]]+0\b"; then
    fail "kli reports 0 receipts — witness regression on CESR-ATTACHMENT handling"
fi
ok "kli stored at least one witness receipt for alice"
```

- [ ] **Step 2: Run the bash test end-to-end against live**

Run:
```bash
cd /Users/seriouscoderone/code/keripy
bash sam-witness/test_live.sh 2>&1 | tail -20
```

Expected (last lines):
```
==> 5. Direct verification: independently exercise witness receipt flow
    ✓ bob = E...
    ✓ 281-byte receipt returned by witness
    ✓ wig stored, signature verifies against witness verfer

Live witness test PASSED against https://witness.keri.host
  alice (kli): E...
  witness:     BNbRfMPQQge6wKO0uof9Y0e_QYOfiF08k9drc6pOgzjt
```

The earlier `! kli reports 0 receipts ...` warn line should be replaced by `✓ kli stored at least one witness receipt for alice`.

If the script exits with `✗ kli reports 0 receipts — witness regression ...`, kli is still not storing receipts despite the deploy. Re-check Task 9 (deploy) and Task 14 (live test_live.py result).

- [ ] **Step 3: Commit**

```bash
cd /Users/seriouscoderone/code/keripy
git add sam-witness/test_live.sh
git commit -m "$(cat <<'EOF'
test(witness): promote receipt-count check to hard fail

Phase 2 wires CESR-ATTACHMENT support; kli incept --receipt-endpoint
must now produce a stored witness receipt against the live witness.
Replace the earlier warn-and-continue with a fail-fast assertion so
any regression is caught immediately.
EOF
)"
```

---

## Task 16: Update Phase 2 success criterion + close-out

**Files:** No file changes. Push and summarize.

**Why:** Phase 2 is complete; push the commits to the fork and confirm everything matches the design's success criteria.

- [ ] **Step 1: Push to fork**

```bash
cd /Users/seriouscoderone/code/keripy
git push fork main
```

Expected: push succeeds.

- [ ] **Step 2: Summarize**

Confirm the following are true:
- Local: 98 of 98 tests in `tests/app/test_lambding.py` + `tests/db/test_dynamodbing.py` still pass.
- Live: 10 of 10 tests in `sam-witness/test_live.py` pass against `https://witness.keri.host`.
- Live: `bash sam-witness/test_live.sh` passes including the `✓ kli stored at least one witness receipt for alice` assertion.
- `kli status --name alice --alias alice --verbose` (after a `kli incept --receipt-endpoint --wits BNbRfMPQQge6wKO0uof9Y0e_QYOfiF08k9drc6pOgzjt --toad 1`) reports `Receipts: 1`.

Phase 2 is done. The witness now accepts the standard kli/signify/keria HTTP format, signs only events that include it as a witness, persists its own receipts in `db.wigs`, and serves them via `GET /receipts`. Next: Phase 3 (mailbox read endpoints) — see roadmap section.

- [ ] **Step 3: No commit needed (close-out only)**
