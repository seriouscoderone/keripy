# Phase 2 — Receipt Generation Polish + CESR-ATTACHMENT Header Support (Design)

**Roadmap phase:** 2 of 4 (see `2026-04-21-lambda-witness-roadmap.md`)
**Size:** Small
**Status:** Design approved; ready for implementation planning

## Context

Phase 1 conformance testing (`sam-witness/test_live.py`) revealed that our Lambda witness silently fails for every standard KERI client. `kli`, `signify-ts`, and `keria` all use `streamCESRRequests` (`src/keri/app/httping.py:154`) which puts the event Serder in the HTTP body and signatures in a `CESR-ATTACHMENT` header. Our `handle_cesr_ingest` and `handle_receipt_post` only read the body. The witness sees an event with no signatures, escrows it, returns HTTP 204 — and the controller logs "Receipts: 0" with no error to debug from.

The xfail test `test_post_receipts_kli_format` captures this exactly. Our `kli status` after `kli incept --receipt-endpoint` against the deployed witness shows `Receipts: 0`. Until this is fixed, the witness is unusable by every real KERI client.

This phase fixes that, plus four smaller polish items the Phase 1 design called out:

1. CESR-ATTACHMENT header support (the blocking issue above)
2. Replace silent exception swallowing with structured CloudWatch logging
3. Validate that the event's `pre` is in our kevers and that we are listed as a witness before signing
4. Persist generated receipts so `GET /receipts` can return them
5. Re-iterate the cue-draining loop so cascaded receipt cues are handled

## Problem statement

The current handlers (`handle_cesr_ingest`, `handle_receipt_post`) have four distinct correctness issues:

- **Wire format incompatibility:** They build the parser stream from `event["body"]` only. Real clients put attachments in `event["headers"]["CESR-ATTACHMENT"]`. Without concatenating these, signatures never reach the parser.
- **Silent failure:** All exceptions are caught with `except Exception: pass`. Real failures (signature verification failure, bad CESR encoding, internal Kevery errors) leave no trace in CloudWatch.
- **No AID gate:** Receipt cues for any AID get signed and returned. A controller that did not list us in `wits` should not get a witness receipt — it pollutes our DB and may mislead the controller.
- **Misplaced receipt storage:** `GET /receipts?pre=...&sn=...` reads `db.rcts` (non-trans receipt couples by trans receipter), but witness receipts on a controller event live in `db.wigs` (per `eventing.py:4196-4202`). The endpoint returns 404 for receipts that exist.

A fifth issue is structural: the cue-draining loop is a single pass, but secondary receipt cues can arise after `processEscrows()` runs against the just-receipted state. The loop should iterate until quiescent.

## Approach (approved)

**Surgical patches with two small helpers in one file.** Mirror the canonical extraction logic of `parseCesrHttpRequest` (`src/keri/app/httping.py:80`) — but lenient about which format the client uses (header concat preferred, inline-CESR-in-body still accepted for backward compat with our existing tests).

Why this approach over alternatives:
- **Not a "ReceiptCycle" service abstraction:** Phase 3 (mailbox) hasn't been designed yet, so locking in the right pipeline shape is premature.
- **Not strict reference mirroring:** The reference returns 412 if `CESR-ATTACHMENT` is missing. Our existing inline tests would all break. Lenient acceptance costs three lines of code and keeps every existing test passing.
- **This approach** changes only `sam-witness/witness_handler.py`. No keripy core changes (Phase 1's lambding extension already attached the Baser methods we need for `db.wigs` access).

## Architecture

### Files touched

| File | Change | LOC |
|------|--------|-----|
| `sam-witness/witness_handler.py` | Add 2 helpers; refactor 3 handlers | ~80 |
| `sam-witness/test_live.py` | Remove `@xfail`, add 2 new tests | ~50 |
| `sam-witness/test_live.sh` | Promote receipt-count check from `warn` to `fail` | ~5 |

No template, env.json, or `lambding.py` changes. No new files.

### Handler structure

```
sam-witness/witness_handler.py
├── (new) _extract_cesr_stream(event)           ← body + CESR-ATTACHMENT concat
├── (new) _drain_receipt_cues(hby, hab)         ← validation, logging, re-iteration
├── handle_cesr_ingest                          ← uses both helpers; returns 204
├── handle_receipt_post                         ← uses both helpers; returns 200+CESR or 204
└── handle_receipt_get                          ← reads db.wigs (was db.rcts)
```

## Components

### `_extract_cesr_stream(event)`

```python
def _extract_cesr_stream(event):
    """Build a CESR ims byte stream from a Lambda HTTP event.

    Supports both equivalent client formats:
      - kli/streamCESRRequests: event Serder in body, signatures in
        the CESR-ATTACHMENT header.
      - Inline: full CESR stream (event + attachments) in body alone.
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

Case-insensitive header lookup because API Gateway can normalize header keys differently between SAM local and the live API.

### `_drain_receipt_cues(hby, hab) -> bytearray`

```python
def _drain_receipt_cues(hby, hab):
    """Drain Kevery cues, generate witness receipts, return concatenated CESR.

    Re-iterates until the cue queue stays empty across a pass — covers the
    case where a receipt itself produces follow-on cues. Validates that
    we're a witness for each pre before signing; logs and skips otherwise.
    All exceptions are logged with traceback.
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
                logger.warning("receipt cue for unknown pre=%s; skipping", serder.pre)
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

### `handle_cesr_ingest` — slimmer, 204 response

```python
def handle_cesr_ingest(event):
    """POST / -- ingest CESR. Generate receipts internally; do not return them."""
    ims = _extract_cesr_stream(event)
    if not ims:
        return response(400, {"error": "empty body"})
    _hby.psr.parse(ims=ims)
    _hby.kvy.processEscrows()
    receipts = _drain_receipt_cues(_hby, _hab)
    if receipts:
        _hby.psr.parse(ims=bytearray(receipts))   # persist witness's own copy
    return response(204, None)
```

### `handle_receipt_post` — slimmer, returns CESR

```python
def handle_receipt_post(event):
    """POST /receipts -- ingest event, return signed receipt as CESR."""
    ims = _extract_cesr_stream(event)
    if not ims:
        return response(400, {"error": "empty body"})
    _hby.psr.parse(ims=ims)
    _hby.kvy.processEscrows()
    receipts = _drain_receipt_cues(_hby, _hab)
    if not receipts:
        return response(204, None)
    _hby.psr.parse(ims=bytearray(receipts))   # persist witness's own copy
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/cesr"},
        "body": base64.b64encode(bytes(receipts)).decode("utf-8"),
        "isBase64Encoded": True,
    }
```

### `handle_receipt_get` — read from `db.wigs`

```python
def handle_receipt_get(event):
    """GET /receipts?pre=...&sn=... -- return witness receipts for pre at sn."""
    params = event.get("queryStringParameters") or {}
    pre = params.get("pre", "")
    if not pre:
        return response(400, {"error": "pre parameter required"})
    sn = int(params.get("sn", "0"))

    dig = _hby.db.kels.getLast(keys=pre, on=sn)
    if dig is None:
        return response(404, {"error": f"no event at pre={pre} sn={sn}"})
    dig = dig.encode("utf-8") if isinstance(dig, str) else dig

    # Witness receipts (this witness's signatures) live in db.wigs, not db.rcts.
    # Kevery routes non-trans receipt couples to wigs when the receiptor is in
    # the AID's wits (see eventing.py:4196-4202).
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

## Data flow

**End-to-end with `kli incept --receipt-endpoint --wits B... --toad 1`:**

```
Controller (kli) ──► POST /receipts
                       Content-Type: application/cesr
                       CESR-ATTACHMENT: <-AABAA...>
                       body: {"v":"KERI10JSON...","t":"icp",...}
                                      │
                                      ▼
Lambda witness  handle_receipt_post(event)
                  ├─► _extract_cesr_stream                    [FIX #1]
                  │     body + header → full CESR ims
                  ├─► _hby.psr.parse(ims)
                  │     → Kevery validates, stores icp+sigs
                  │     → emits "receipt" cue for alice
                  ├─► _hby.kvy.processEscrows()
                  ├─► _drain_receipt_cues                     [FIXES #2,#3,#5]
                  │     for each receipt cue:
                  │       validate pre in kevers              [FIX #3]
                  │       validate hab.pre in kever.wits      [FIX #3]
                  │       try: hab.receipt(serder)
                  │       except: logger.warning(exc_info)    [FIX #2]
                  │     re-loop while producing               [FIX #5]
                  ├─► _hby.psr.parse(receipts) [persist locally]
                  └─► return 200 + CESR receipt
                                      │
                                      ▼
Controller (kli)  Receiptor parses ─► db.wigs[(alice.pre, alice.icp.said)]
                                      kli status: Receipts: 1


GET /receipts?pre=alice&sn=0 ──► handle_receipt_get
                                   ├─► dig = db.kels.getLast
                                   ├─► wigs = db.wigs.get((pre, dig))   [FIX #4]
                                   └─► return 200 {witness_receipts: 1}
```

**Key invariant — witness keeps own copy:** after `_drain_receipt_cues` returns the bytes, the handlers re-parse them through `_hby.psr.parse`. This routes the receipt back through Kevery's reply handlers, which store it in our own `db.wigs` for later `GET /receipts` queries. Without this re-parse, the witness ships receipts but its own DB never sees them.

## Error handling

Status codes and logging policy:

| Endpoint | Condition | Status | Body | Log |
|----------|-----------|--------|------|-----|
| `POST /` | Empty body | 400 | `{"error": "empty body"}` | — |
| `POST /` | Valid CESR (event accepted) | 204 | empty | — |
| `POST /` | Cue for AID we don't witness | 204 | empty | `info` (skip noted) |
| `POST /` | `hab.receipt` raises | 204 | empty | `warning` with traceback (handled inside `_drain_receipt_cues`) |
| `POST /` | `Parser.parse` raises | 500 | `{"error": ...}` | None today — handler dispatcher's top-level `except` doesn't log; out of scope for Phase 2 |
| `POST /receipts` | Empty body | 400 | `{"error": "empty body"}` | — |
| `POST /receipts` | Valid CESR, receipts produced | 200 | base64 CESR | — |
| `POST /receipts` | Valid CESR, no receipts (not our AID) | 204 | empty | `info` |
| `POST /receipts` | `Parser.parse` raises | 500 | `{"error": ...}` | None today — out of scope for Phase 2 |
| `GET /receipts` | Missing `pre` param | 400 | `{"error": "pre parameter required"}` | — |
| `GET /receipts` | Unknown event at (pre, sn) | 404 | `{"error": "no event at pre=... sn=..."}` | — |
| `GET /receipts` | Event found, no witness receipts | 404 | `{"error": "no witness receipts found"}` | — |
| `GET /receipts` | Receipts exist | 200 | `{pre, sn, witness_receipts, witness_aid}` | — |

**Logging:** all `logger.warning(..., exc_info=True)` so CloudWatch shows the full traceback. `logger.info` for non-error skip cases (cue for unknown AID, cue for AID we don't witness). No `print` statements.

**Behavioral change worth noting:** `POST /` now returns **204 with empty body** (was `200 {"processed": True, "receipts_generated": N}`). The current JSON has no consumers we control — only test fixtures. Update test_live.py to expect 204.

## Testing

**Layer 1 — regression:**
```bash
python3 -m pytest tests/app/test_lambding.py tests/db/test_dynamodbing.py -q
# Expect: 98 passed (no keripy core touched)
```

**Layer 2 — local SAM:**
```bash
sam build --template sam-witness/template.yaml --use-container
docker tag witnessfunction:latest witness-handler:latest
sam local invoke WitnessFunction --template sam-witness/template.yaml \
    --env-vars sam-witness/env.json --event sam-witness/events/oobi-get.json
# Expect: existing OOBI test still passes (handler refactor must not regress Phase 1)
```

**Layer 3 — live conformance** (`sam-witness/test_live.py`, post-deploy):

All 7 existing tests must continue to pass. The xfail must turn green:

```python
# Remove this decorator from test_post_receipts_kli_format:
@pytest.mark.xfail(reason="Phase 2: ...", strict=True)
```

**Two new tests added:**

```python
def test_get_receipts_after_post(fresh_hby, witness_pre):
    """After POSTing a controller's inception, GET /receipts returns the
    stored witness signatures. Verifies fix #4 (db.wigs vs db.rcts)."""
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))
    bob = fresh_hby.makeHab(name="bob", transferable=True,
                            isith="1", icount=1, ncount=1, nsith="1",
                            toad=1, wits=[witness_pre])
    body, attachment = split_event_and_attachment(bob.makeOwnInception())
    req = urllib.request.Request(
        f"{WITNESS_URL}/receipts", data=body,
        headers={"Content-Type": "application/cesr",
                 "CESR-ATTACHMENT": attachment.decode("utf-8")},
        method="POST")
    urllib.request.urlopen(req, timeout=TIMEOUT).read()

    status, _, body = http_get(f"/receipts?pre={bob.pre}&sn=0",
                                accept="application/json")
    assert status == 200
    data = json.loads(body)
    assert data["pre"] == bob.pre
    assert data["witness_receipts"] >= 1
    assert data["witness_aid"] == witness_pre


def test_post_does_not_receipt_unrelated_aid(fresh_hby, witness_pre):
    """An inception that does not list the witness in wits should not
    get a witness receipt back. Verifies fix #3 (AID validation)."""
    bob = fresh_hby.makeHab(name="bob", transferable=True,
                            isith="1", icount=1, ncount=1, nsith="1")
    # bob has no witnesses; witness should refuse to receipt
    status, _, body = http_post_cesr("/receipts", bob.makeOwnInception())
    assert status in (200, 204)
    assert b'"t":"rct"' not in body, (
        f"witness signed for an AID that does not list it as witness: {body[:80]!r}"
    )
```

**Bash smoke test** (`sam-witness/test_live.sh`): the current `warn` for "kli reports 0 receipts" is promoted to `fail`. After Phase 2, `kli status --verbose` must show `Receipts: 1` after `kli incept --receipt-endpoint`.

## Failure modes to catch during review

- `_extract_cesr_stream` returns empty `ims` for valid POSTs (header lookup case mismatch).
- `_drain_receipt_cues` re-iteration loops forever (escrow re-evaluation produces same cues each pass).
- `handle_cesr_ingest` returns 200 + body when it should be 204 + empty.
- `handle_receipt_get` looks up `dig` in the wrong KEL store (must use `db.kels.getLast(pre, on=sn)`).
- API Gateway's `Content-Type: application/cesr` body decoding when client sends `Accept: */*` (the Phase 1 quirk — should still work since we only changed handler logic, not template).

## Existing functions reused

| Function | Location | Role |
|----------|----------|------|
| `Hab.receipt` | `src/keri/app/habbing.py:1587` | Sign witness receipt for a serder |
| `Hab.makeOwnInception` | `src/keri/app/habbing.py` | Build the controller's own icp message (test only) |
| `Parser.parse` (via `_hby.psr.parse`) | — | Route CESR to Kevery / Revery |
| `Kevery.processEscrows` | `src/keri/core/eventing.py:5610` | Resolve escrowed events |
| `Kevery.cues` | `src/keri/core/eventing.py:3785` | Receipt cue queue |
| `Baser.kels.getLast` | `src/keri/db/basing.py` | Look up event digest at (pre, sn) |
| `Baser.wigs.get` | `src/keri/db/basing.py` | Witness signatures for an event |

No new keripy code is needed. Phase 1's `setup_baser` extension already attaches the Baser methods we use.

## Implementation deviation (Task 15a)

**Discovered during conformance testing** that the original spec's response shape was wrong. The plan called for Lambda to return CESR with `isBase64Encoded: True` and `body: base64.b64encode(...)` — relying on AWS API Gateway to base64-decode the body before sending to clients. Pytest tests passed because they explicitly set `Accept: application/cesr`. Real KERI clients (`kli`, `signify-ts`, `keria`) all use the default `Accept: */*` which does **not** trigger API Gateway's binary-content path. They received the base64 text and their CESR parsers silently failed to find frames.

**Resolved** by returning CESR as plain text (no `base64.b64encode`, no `isBase64Encoded`) since CESR's qb64 form is pure ASCII. API Gateway then sends the body unchanged regardless of Accept. This change applies to both `handle_receipt_post` and `handle_oobi_get`. Caveat: if a future change emits qb2 (binary CESR) instead of qb64, the response shape must restore base64.

## Out of scope (later phases)

- Mailbox-based receipt delivery (`/mailbox/{aid}` GET endpoint) — Phase 3.
- Watcher-style key state notices (`/ksn`) — separate `sam-watcher` Lambda.
- Replacing `_clear_keeper` workaround with transactional Manager.incept — Phase 4.
- Per-controller authorization (TOTP / OAuth-style witness gating) — out of roadmap entirely.
