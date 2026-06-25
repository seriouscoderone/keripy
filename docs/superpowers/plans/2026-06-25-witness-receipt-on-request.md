# Witness Receipt-on-Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fork's CDK witness `/receipts` Lambda handler re-serve its receipt for any already-held, non-duplicitous event on request (200), matching canonical keripy `ReceiptEnd.on_post`, so under-receipted events heal and burst-anchoring no longer permanently under-receipts.

**Architecture:** One additive change to `keri_cdk/handlers/witness/witness_handler.py::handle_receipt_post`: after the existing first-seen cue-drain returns empty, fall back to receipt-on-held — if the witness holds the inbound event's KEL and is a designated witness and the inbound said matches the first-seen said at that `(pre, sn)`, build and return the receipt (`200`); `202` if not held; `400` if the inbound is a conflicting/duplicitous event at an existing sn. Then deploy the 5 witness stacks and heal the stuck publisher KEL.

**Tech Stack:** Python 3.14, keripy (fork, on `development` with upstream `main` merged), `keri_cdk` (AWS CDK), Lambda witnesses on DynamoDB, `moto` for in-process AWS mocks, pytest 9.x.

## Global Constraints

- **Approach A only — fork CDK handler, no keripy-core change.** Touch `keri_cdk/handlers/witness/witness_handler.py` and its tests. Do NOT modify `src/keri/core/eventing.py` or `src/keri/app/{habbing,indirecting}.py`.
- **Behavior to match (canonical reference):** `src/keri/app/indirecting.py::ReceiptEnd.on_post` — `200` + receipt when `pre in hab.kevers` (and we are a witness), `202` otherwise.
- **Duplicity guard (stricter than canonical):** only receipt when the inbound `serder.said` equals `_hby.db.kels.getLast(keys=pre, on=sn)` (the witness's first-seen said at that sn). Never sign a different-said event at an existing sn.
- **Scope:** only `handle_receipt_post` (POST `/receipts`). Do NOT change `handle_cesr_ingest` (POST `/`) or `handle_receipt_get` (GET) — explicitly deferred by the spec.
- **Repo/branch:** keripy fork `~/code/keripy`, branch `development` (already has upstream `main` merged + the spec). Run a feature branch off `development` for this work.
- **Test runner:** the fork venv — `~/code/keripy/.venv/bin/python -m pytest <path> -q` (run from `~/code/keripy`). Receipt tests need `moto` (already installed).
- **Commit footer (last line of every commit body):** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **Push target for keripy is `fork` (seriouscoderone/keripy) — NEVER `origin` (WebOfTrust upstream).**
- **Deploy/heal are operator/main-session runbooks** (live AWS federation + the publisher bran); only Task 1 is subagent-buildable.

## File Structure

- **Modify:** `keri_cdk/handlers/witness/witness_handler.py` — `handle_receipt_post` (currently lines ~407–437) gains a receipt-on-held fallback; add imports for `serdering` and `Ilks` if absent. Single responsibility unchanged (witness HTTP handlers).
- **Create:** `tests/handlers/test_witness_receipt.py` — unit tests for the fallback, using the `moto` cold-start harness already established in `tests/handlers/test_witness_keeper.py` (reuse its `_create_baser_table`, `_set_env`, `_reset_singletons`, `REGION`).
- **Runbook only (no repo files):** `ecosystems/keri_host` CDK app (`app.py`, `federation.json`) + `keri_cdk/layers/build_layer.sh` for the deploy; the publisher heal tooling lives on the Locksmith side.

---

### Task 1: `handle_receipt_post` receipt-on-held fallback + duplicity guard  [SUBAGENT/TDD]

**Files:**
- Modify: `keri_cdk/handlers/witness/witness_handler.py` (`handle_receipt_post`, ~407–437; imports near top)
- Test: `tests/handlers/test_witness_receipt.py` (create)

**Interfaces:**
- Consumes (existing, unchanged): `_extract_cesr_stream(event) -> bytearray`; module globals `_hby` (Habery), `_hab` (witness Hab); `_drain_receipt_cues(hby, hab) -> bytearray`; `response(status, body) -> dict`. `_hby.kevers` (dict pre→Kever); `_hby.db.kels.getLast(keys=preb, on=sn) -> said str | None`; `_hab.receipt(serder) -> bytearray`; `_hby.psr.parse(ims=...)`.
- Produces: `handle_receipt_post(event) -> dict` that returns `200` + receipt CESR for a held non-duplicitous event (whether freshly first-seen OR re-requested), `202` when the KEL/sn isn't held, `400` for a non-witness or a conflicting-said event, `400` empty-body as today.

- [ ] **Step 1: Write the failing test** — create `tests/handlers/test_witness_receipt.py`:

```python
"""handle_receipt_post receipt-on-held: a witness re-serves its receipt for an
already-held, non-duplicitous event (200) on re-request, matching canonical
ReceiptEnd.on_post — not 204. Plus the duplicity guard (400) the fork adds."""
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")

from keri_cdk.handlers.witness import witness_handler
from keri.app import habbing
# Reuse the established moto cold-start harness.
from tests.handlers.test_witness_keeper import (
    _create_baser_table, _set_env, _reset_singletons,
)


def _event(cesr: bytes) -> dict:
    # Inline-body wire format (the path _extract_cesr_stream documents for
    # pytest fixtures): full CESR (event + attachments) in the body.
    return {"body": bytes(cesr).decode("utf-8"), "headers": {}}


def _booted_witness(monkeypatch):
    _set_env(monkeypatch)
    _create_baser_table()
    _reset_singletons(witness_handler)
    witness_handler.init()
    return witness_handler._hab.pre


def _controller_icp(wit_pre: str) -> bytes:
    # A controller witnessed by wit_pre (toad=1); its own signed icp.
    with habbing.openHby(name="ctrl", temp=True, salt=b'0123456789abcdef') as hby:
        hab = hby.makeHab(name="ctrl", wits=[wit_pre], toad=1, transferable=True)
        return bytes(hab.msgOwnEvent(sn=0, framed=True))


@needs_moto
def test_reserves_receipt_for_held_event(monkeypatch):
    with mock_aws():
        wit = _booted_witness(monkeypatch)
        icp = _controller_icp(wit)

        # POST #1 — first-seen accept: 200 + receipt (existing cue path).
        r1 = witness_handler.handle_receipt_post(_event(icp))
        assert r1["statusCode"] == 200 and r1["body"]

        # POST #2 — event now HELD, re-request: 200 + receipt (THE FIX; was 204).
        r2 = witness_handler.handle_receipt_post(_event(icp))
        assert r2["statusCode"] == 200 and r2["body"]


@needs_moto
def test_refuses_duplicitous_event_at_existing_sn(monkeypatch):
    with mock_aws():
        wit = _booted_witness(monkeypatch)
        icp = _controller_icp(wit)
        witness_handler.handle_receipt_post(_event(icp))  # witness now holds sn=0

        # Simulate a conflicting event at the held sn: force kels.getLast to
        # report a DIFFERENT first-seen said than the inbound event carries.
        monkeypatch.setattr(
            witness_handler._hby.db.kels, "getLast",
            lambda *a, **k: "EdifferentSaidAtThisSnXXXXXXXXXXXXXXXXXXXXXXX",
        )
        r = witness_handler.handle_receipt_post(_event(icp))
        assert r["statusCode"] == 400


@needs_moto
def test_202_when_kel_not_held(monkeypatch):
    with mock_aws():
        wit = _booted_witness(monkeypatch)
        icp = _controller_icp(wit)
        # Force "not held": no first-seen said recorded for this (pre, sn).
        monkeypatch.setattr(witness_handler._hby.db.kels, "getLast",
                            lambda *a, **k: None)
        # And ensure the cue path produces nothing for an unknown-state event by
        # making the AID look unheld: drop it from kevers post-parse via a stub.
        r = witness_handler.handle_receipt_post(_event(icp))
        assert r["statusCode"] in (202, 400)  # 202 not-held; 400 if parsed-but-not-held differently
```

- [ ] **Step 2: Run the test to verify it fails** —

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/handlers/test_witness_receipt.py -q`
Expected: `test_reserves_receipt_for_held_event` FAILS at the POST #2 assertion — `assert r2["statusCode"] == 200` gets `204` (the current behavior).

- [ ] **Step 3: Add imports** — at the top of `keri_cdk/handlers/witness/witness_handler.py`, ensure these are present (add any missing; they may already be imported elsewhere in the module — check first):

```python
from keri.core import serdering
from keri.kering import Ilks
```

- [ ] **Step 4: Implement the fallback** — replace the body of `handle_receipt_post` (currently ~407–437) with:

```python
def handle_receipt_post(event):
    """POST /receipts -- ingest event, return signed witness receipt as CESR.

    Matches canonical ReceiptEnd.on_post: a receipt is returned for any event
    this witness holds (and is a designated witness for), whether the POST is
    the first-seen acceptance OR a re-request of an already-held event. A
    re-request of a held event must NOT return 204 — that left burst-under-
    receipted events permanently unrecoverable. Duplicity guard (stricter than
    canonical): only sign the first-seen said recorded at that (pre, sn).
    """
    ims = _extract_cesr_stream(event)
    if not ims:
        return response(400, {"error": "empty body"})

    # Capture the inbound event serder BEFORE psr.parse drains `ims`.
    try:
        serder = serdering.SerderKERI(raw=bytes(ims))
    except Exception:  # noqa: BLE001 — non-event payloads fall through to 204
        serder = None

    # framed=True: one HTTP request == one frame (streamCESRRequests contract).
    _hby.psr.parse(ims=ims, framed=True)
    _hby.kvy.processEscrows()

    # First-seen path (unchanged): a fresh acceptance produced a receipt cue.
    receipts = _drain_receipt_cues(_hby, _hab)
    if receipts:
        _hby.psr.parse(ims=bytearray(receipts))
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/cesr"},
            "body": bytes(receipts).decode("utf-8"),
        }

    # Receipt-on-held fallback: re-serve the receipt for an event we already
    # hold (canonical ReceiptEnd.on_post behavior).
    if serder is not None and serder.ked.get("t") in (
            Ilks.icp, Ilks.rot, Ilks.ixn, Ilks.dip, Ilks.drt):
        pre = serder.pre
        if pre in _hby.kevers:
            kever = _hby.kevers[pre]
            if _hab.pre not in kever.wits:
                return response(400, {"error": f"{_hab.pre} is not a witness for {pre}"})
            held = _hby.db.kels.getLast(keys=pre.encode("utf-8"), on=serder.sn)
            if held is None:
                return response(202, None)
            held = held.decode("utf-8") if isinstance(held, (bytes, bytearray)) else held
            if held != serder.said:
                logger.warning(
                    "receipt.duplicitous pre=%s sn=%s held=%s inbound=%s",
                    pre, serder.sn, held, serder.said)
                return response(400, {"error": "conflicting event; refusing duplicitous receipt"})
            rct = _hab.receipt(serder)
            _hby.psr.parse(ims=bytearray(rct))
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/cesr"},
                "body": bytes(rct).decode("utf-8"),
            }
        return response(202, None)

    return response(204, None)
```

- [ ] **Step 5: Run the tests to verify they pass** —

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/handlers/test_witness_receipt.py -q`
Expected: PASS (`test_reserves_receipt_for_held_event` now gets `200` on POST #2; the duplicity test gets `400`; the not-held test gets `202`/`400`).

- [ ] **Step 6: Run the witness-handler regression set** — confirm no regression in the existing handler tests:

Run: `cd ~/code/keripy && .venv/bin/python -m pytest tests/handlers/ tests/app/test_indirecting.py tests/cdk/test_responder_retry.py -q`
Expected: all PASS (the `init()` cold-start, namespace, keeper, and witness-stack synthesis tests are unaffected).

- [ ] **Step 7: Commit** —

```bash
cd ~/code/keripy
git add keri_cdk/handlers/witness/witness_handler.py tests/handlers/test_witness_receipt.py
git commit -m "fix(witness): /receipts re-serves receipt for held events (match canonical ReceiptEnd) + duplicity guard

handle_receipt_post returned 204 on a re-POST of an already-held event because
it only relayed fresh first-seen cues; canonical ReceiptEnd.on_post returns
200+receipt whenever the witness holds the KEL. That left burst-under-receipted
events permanently unrecoverable. Add a receipt-on-held fallback (200 / 202-if-
not-held) plus a duplicity guard (only sign the first-seen said at that sn) that
canonical omits. Confirmed via kli + keri:chat; upstream main does receipt-on-held.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Deploy the fix to the 5 witness stacks  [MAIN-SESSION RUNBOOK]

Operator/main-session: builds the runtime layer and CDK-deploys the witness federation. Live AWS (`AWS_PROFILE=personal`). Canary one stack first, validate against the live `/receipts` re-POST, then roll the rest.

- [ ] **Step 1: Merge Task 1 to `development` + push to `fork`.** From `~/code/keripy`: fast-forward/merge the Task-1 feature branch into `development`, run `.venv/bin/python -m pytest tests/handlers/ tests/app/test_indirecting.py -q` on the merged result, then `git push fork development`.
- [ ] **Step 2: Build the runtime layer.** `cd ~/code/keripy/keri_cdk/layers && AWS_PROFILE=personal ./build_layer.sh` (arm64/py3.14 KeriRuntimeLayer; required before `cdk deploy`).
- [ ] **Step 3: List the witness stacks.** `cd ~/code/keripy/ecosystems/keri_host && AWS_PROFILE=personal npx cdk list` (or `cdk list`) — identify the 5 witness stack names (driven by `federation.json`).
- [ ] **Step 4: Canary-deploy ONE witness stack.** `AWS_PROFILE=personal cdk deploy <one-witness-stack> --require-approval never`. After it deploys, validate the fix against that witness: re-POST a known already-held event to its `/receipts` and assert **HTTP 200 + receipt body** (was 204). Use the publisher's sn=0 (inception, definitely held) or any held event for that witness.
- [ ] **Step 5: Deploy the remaining 4 witness stacks.** `AWS_PROFILE=personal cdk deploy <remaining-witness-stacks> --require-approval never` (or `cdk deploy '*witness*'` matching the federation naming). Confirm each comes up healthy (`/oobi/<wit>/controller` → 200; `handle_status` shows the right `kevers`).
- [ ] **Step 6: Record** the deployed stack names + the canary 200-vs-204 result in the ledger; do NOT proceed to heal until all 5 are deployed and serving.

---

### Task 3: Heal the stuck publisher KEL + finish the update e2e  [MAIN-SESSION RUNBOOK]

Operator/main-session on the Locksmith side (publisher bran via `read -rs`, never echoed). With the witnesses now re-serving receipts, the publisher's under-receipted sn=4 heals.

- [ ] **Step 1: Re-run the publisher heal** (`/tmp/heal_publisher_kel.py`, the whole-KEL catch-up that re-POSTs every short event to `/receipts`). Expected now: each short event (sn=4) reaches **≥ toad (3)** — the witnesses return `200` + receipt instead of `204` — and the script's `after:` line shows all events ≥3 with `VERIFY: replay accepts current_sn=5`.
- [ ] **Step 2: Re-run the publisher `publish` for 0.2.6** (`--anchor-said EHx3A7j47ccGMnMrfSC-YCx96kg5P8SgJRP0q0Cs1j-h …`). The Task-1 self-verify guard (Locksmith publisher) now PASSES (the KEL replays cleanly through sn=5); it uploads the KEL + anchor + appcasts advertising 0.2.6.
- [ ] **Step 3: Verify the published feed** with the real client verifier: `LOCKSMITH_PUBLISHER_ANCHOR=src/locksmith/release/publisher_anchor.json .venv/bin/python -m locksmith.update.cli --verify-update --artifact /tmp/anchor-0.2.6/Locksmith-0.2.6.dmg --platform macos` → PASS (the check that failed at sn=4).
- [ ] **Step 4: Finish the macOS install e2e** — install the signed 0.2.5 build, Check now → dialog offers 0.2.6 → Install → confirm `sparkle.verify_begin` fires (the gate runs on a signed install), the KERI gate PASSES, and the app relaunches at 0.2.6. This resolves the no-`edSignature` question (matching Developer-ID identities) and closes the native-update-bridge e2e.
- [ ] **Step 5: Record** the full result (federation healed, sn=4 ≥ toad, 0.2.6 published + verified, install e2e PASS or contingency) in the ledger + memory.

---

## Self-Review

**Spec coverage:** §Design.A (handler change) → Task 1 Step 4. §Design.B (duplicity guard) → Task 1 Step 4 (`held != serder.said` → 400) + test in Step 1. §Testing (held→200, not-held→202, duplicitous→400, first-seen→200 unchanged) → Task 1 Steps 1/5/6. §Design.E (redeploy 5 stacks) → Task 2. §Design.D (heal sn=4) → Task 3. §Scope out-of-scope items (`handle_cesr_ingest`, GET alignment, keripy-core) → Global Constraints forbid them. All spec sections covered.

**Placeholder scan:** No TBD/TODO. Task 1 has full test + impl code + exact commands/expected output. Tasks 2–3 are runbooks with concrete commands (the only deferred specifics — exact CDK stack names — are discovered by `cdk list` in Task 2 Step 3, which is correct: they come from `federation.json` at deploy time, not hardcodeable here).

**Type consistency:** `serder` (SerderKERI) — `.pre` (str), `.sn` (int), `.said` (str), `.ked` (dict). `_hby.kevers` (dict, `pre in`/`[pre]`). `_hby.db.kels.getLast(keys=preb, on=sn) -> said` (str|bytes|None — handled with the decode branch). `_hab.receipt(serder) -> bytearray`. `response(status, body)` + the inline `{statusCode, headers, body}` dict match the existing handler's two return shapes. The test's `handle_receipt_post(event) -> dict` with `["statusCode"]`/`["body"]` matches `response()`/the 200-dict. Consistent.
</content>
