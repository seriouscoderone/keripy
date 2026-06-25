# Witness Receipt-on-Request — Design

**Date:** 2026-06-25
**Status:** Approved (brainstorm) — ready for implementation plan
**Repo:** keripy fork (`~/code/keripy`); change is in the CDK witness Lambda handler
**Relates to:** `2026-06-19-witness-ddb-first-seen-concurrency-design.md` (the DynamoDB first-seen gate), `2026-06-18-sam-to-cdk-federation-cutover-design.md` (the live 5×5 federation). Surfaced by the Locksmith publisher e2e (a release KEL stuck at an under-receipted sn).

## Summary

A CDK/Lambda witness only returns its receipt when a **fresh first-seen acceptance cue** fires for an event. On a re-POST of an event the witness has *already accepted*, the handler returns **`204` empty** and emits no receipt. This violates canonical keripy + the KERI spec: a witness stores its receipt in its immutable KERL and must **serve it on request** so controllers can reach `toad` after timing races or partitions. The fix makes the fork's `/receipts` Lambda handler match canonical `ReceiptEnd.on_post` — receipt-on-request for any **held, non-duplicitous** event — plus a duplicity guard canonical itself omits. This heals already-under-receipted events (a publisher KEL stuck at sn=4 with 2/3 receipts) and prevents recurrence under burst anchoring, with no change to keripy core.

## Context — observed behavior (the live keri.host federation, 2026-06-25)

- A publisher AID's KEL had per-event witness-receipt counts `[(0,5),(1,5),(2,4),(3,3),(4,2),(5,5)]` — **sn=4 stuck at 2/toad=3**. The verifier (and the publisher's self-verify guard) replay from sn=0 and escrow at sn=4, so **no release ≥ sn=4 can publish**.
- The decline across the burst-cut sn=2/3/4 then recovery at the isolated sn=5 indicates witnesses **fell behind under burst** at creation — fewer than `toad` receipted synchronously — while sn=5 (cut in isolation) got 5/5.
- Re-POSTing sn=4 to all 5 witnesses' `/receipts` now returns **HTTP 204 empty, zero receipts collected**, including from the 2 witnesses we already hold receipts from. All 5 *hold* sn=4 (they hold sn=5, impossible without sn=0–4), yet refuse to (re)issue a receipt.
- **`kli` reproduction (this design's confirmation).** A local **canonical** keripy witness (`kli init` non-transferable witness + `kli witness start`), fed a controller icp at `/receipts` three times: POST#1 (first-seen) → `200` + 281-byte receipt; POST#2 and #3 (event now **held**) → **`200` + 281-byte receipt every time**. Canonical keripy re-serves; the fork returns `204`. The divergence is empirically proven.
- **`keri:chat` spec confirmation.** "First seen" gates *duplicity* (which version a witness accepts), **not** receipt availability: a witness stores its receipt in its immutable KERL and must provide it on request; receipt backfill is the standard recovery for timing-race under-receipting. "Witnesses only issuing receipts once, synchronously, would make KERI operationally fragile." It also confirms the converse: a witness must **reject any conflicting (different-said) event at the same sequence number** as duplicitous — never endorse it.

## Root cause (code-confirmed, file:line)

- **Fork handler — `keri_cdk/handlers/witness/witness_handler.py`:** `handle_receipt_post` (lines 407–437) returns only what `_drain_receipt_cues` (312–352) produces. `_drain_receipt_cues` consumes `kin=="receipt"` cues from the Kevery. That cue is pushed **only on fresh in-order first-seen acceptance** (`src/keri/core/eventing.py` 4349–4352). A re-POST of a known event takes the duplicate-but-not-duplicitous path (`eventing.py` 4366–4387) which re-logs the event but **pushes no receipt cue** → `_drain_receipt_cues` returns empty → `handle_receipt_post` returns `204` (line 423–424).
- **Canonical reference — `src/keri/app/indirecting.py` `ReceiptEnd.on_post` (1072–1121):** parses the event, then **if `pre in self.hab.kevers` and `self.hab.pre in kever.wits`, calls `self.hab.receipt(serder)` and returns `200` + the receipt** — *independent of any cue*; returns **`202`** if the KEL isn't held yet. This is the behavior the fork handler must match.
- **`Hab.receipt` has no duplicity guard — `src/keri/app/habbing.py` 1728–1779:** it builds a receipt over whatever `serder` it is handed (`reserder = eventing.receipt(pre, sn, said=serder.said)`, then signs `serder.raw`) with **no check that the serder is the witness's held/first-seen version**. So canonical `ReceiptEnd` would, strictly, receipt a duplicitous re-POST. Our fork fix MUST add the guard canonical omits.
- **NOT a version regression — verified against latest upstream.** `WebOfTrust/keripy origin/main` (HEAD `d9e9c6c6`, 2026-06-25) still has `ReceiptEnd.on_post` = receipt-on-held: `200` when `pre in hab.kevers` (and we're a witness), `202` otherwise. `keri_cdk/` is **absent upstream** — added by fork commits `3db5a678` (scaffold) / `87edf13a` (relocate handlers). So `handle_receipt_post` is a **fork-authored Lambda reimplementation** that diverged from upstream's own `ReceiptEnd`; the fork's library `src/keri/app/indirecting.py ReceiptEnd` is itself unchanged from upstream. The fix restores, in the Lambda handler, the behavior upstream already ships.

## Locked decisions (from brainstorm)

1. **Approach A — fix the fork's Lambda `/receipts` handler only.** No keripy-core (`eventing.py`) change; the fork deliberately minimizes core divergence from upstream. This restores behavior canonical keripy's library layer (`ReceiptEnd`) already has.
2. **Scope to `handle_receipt_post` (POST `/receipts`).** Both clients that collect receipts use it — the publisher (`kli … --receipt-endpoint` → `agenting.Receiptor`) and the Locksmith wallet (`LocksmithReceiptor`, which POSTs to `/receipts`). `handle_cesr_ingest` (POST `/`) is out of scope.
3. **Add a duplicity guard canonical lacks:** only receipt when the inbound `serder.said` equals the witness's first-seen said at that `(pre, sn)`.
4. **Heal the stuck sn=4 after deploy** by re-running the publisher heal (re-POST → `200` + receipts → ≥toad), then publish and finish the macOS update e2e.
5. **Redeploy all 5 witness stacks** (shared handler).

## Design

### A. The handler change — `handle_receipt_post` (`keri_cdk/handlers/witness/witness_handler.py`)

Keep the existing parse + escrow + cue-drain (so a genuine first-seen POST still returns its freshly-built receipt as today). Then add a **receipt-on-held fallback** that mirrors `ReceiptEnd.on_post`:

1. Extract the inbound event serder from the parsed stream: `serder = serdering.SerderKERI(raw=bytes(ims))` (the leading sized event; same value `ReceiptEnd` derives from `cr.payload`). Validate `serder.ked["t"]` ∈ {`icp`,`rot`,`ixn`,`dip`,`drt`} as canonical does (reject other ilks with `400`).
2. `_hby.psr.parse(ims, framed=True)` + `_hby.kvy.processEscrows()` (as today) so the witness accepts the event if new.
3. `receipts = _drain_receipt_cues(_hby, _hab)`. If non-empty → return `200` + receipts (unchanged first-seen path).
4. **Fallback (new):** if `receipts` is empty:
   - `pre = serder.pre`; if `pre not in _hby.kevers` → **`202`** (KEL not held yet; caller falls back to polling), matching canonical.
   - `kever = _hby.kevers[pre]`; if `_hab.pre not in kever.wits` → **`400`** (not a designated witness), matching canonical.
   - **Duplicity guard:** `held = _hby.db.kels.getLast(keys=pre.encode(), on=serder.sn)`. If `held` is `None` → `202`. If `held != serder.said` → **`400`** "conflicting event at sn={sn}; refusing to receipt duplicitous event" (and log) — **never sign a non-held version**.
   - Else (held == serder.said): `rct = _hab.receipt(serder)`; re-parse it into `db.wigs` (`_hby.psr.parse(bytearray(rct))`, as the existing 200 path does at line 427); return **`200`** + `rct` (`Content-Type: application/cesr`).

The fallback is additive: the existing first-seen and 204/202 control flow is preserved except that a held, matching, non-duplicitous event now yields `200` + receipt instead of `204`.

### B. Why the duplicity guard (canonical omits it)

`Hab.receipt` signs any serder. Canonical `ReceiptEnd` relies on the prior `parseOne` to escrow a duplicitous event but still calls `hab.receipt(inbound_serder)` unconditionally when `pre in kevers` — so it could endorse a conflicting event. The fork handler will be stricter: it signs only the said it has recorded as first-seen at that sn (`db.kels.getLast`), upholding "first seen, always seen — never endorse a conflicting version" (the `keri:chat`/spec principle). This is a security hardening over canonical, at one extra strong point-read.

### C. Out of scope (YAGNI)

- **`handle_cesr_ingest` (POST `/`):** not changed — receipt-collecting clients use `/receipts`.
- **`handle_receipt_get` (GET `/receipts?pre=&sn=`):** today returns a *count* (diverges from canonical `on_get`, which serves the stored wig CESR). Aligning it is a useful follow-up for pull-based backfill but is **not required** for this fix (the POST path heals sn=4 and the recurrence). Noted, deferred.
- **Client-side burst-staggering of anchor POSTs:** unnecessary once the witness re-serves receipts — the publisher's `_wait_for_receipts` re-collect (Locksmith side) then backfills as witnesses catch up. Out of scope here.

### D. Heal the stuck sn=4 (post-deploy, operational)

After the 5 witness stacks are redeployed: re-run the publisher heal (re-POST every under-receipted event to `/receipts`; witnesses now return `200` + receipt → each event reaches ≥toad), re-export the KEL, confirm `replay_kel` accepts through the tip, then publish the pending release and complete the macOS install e2e. No code in this repo beyond the handler; the heal tooling lives on the Locksmith side.

### E. Deploy

CDK redeploy of all **5 witness stacks** (`keri_cdk` witness construct; they share `witness_handler.py`). No reserved-concurrency or table change. The mailbox/Service-AID stacks are untouched.

## Testing

- **Unit (handler):** with a witness `_hby`/`_hab` and a controller whose event the witness holds:
  - held + matching said → **`200`** with a non-empty receipt body; the wig is persisted to `db.wigs`.
  - same AID, **different** said at an existing sn → **`400`**, no receipt produced, no `db.wigs` write.
  - AID/sn not held → **`202`**.
  - non-witness for the AID → **`400`**.
  - genuine first-seen POST → still **`200`** via the existing cue path (no regression).
- **Regression scenario (documented):** the `kli` reproduction in this spec — local canonical witness re-serves on POST#2/#3 — encodes the expected behavior; the fork handler must match it.
- **Live validation:** after redeploy, the publisher heal drives the stuck event to ≥toad and the Locksmith `verify_artifact` accepts the published KEL (the check that failed at sn=4).

## Scope

- **In:** `handle_receipt_post` receipt-on-held fallback + duplicity guard; unit tests; redeploy of the 5 witness stacks; the operational heal of the stuck event.
- **Out:** any keripy-core (`eventing.py`/`habbing.py`) change; `handle_cesr_ingest`; `handle_receipt_get` count→CESR alignment (deferred follow-up); client-side anchor pacing; mailbox/SSE receipt delivery (a separate valid path, not needed here).

## Definition of done

`handle_receipt_post` returns `200` + receipt for a held, non-duplicitous event on re-request, `202` when the KEL isn't held, and `400` for a conflicting/duplicitous event or a non-witness; unit tests cover all four; the 5 witness stacks are redeployed; the previously stuck under-receipted event reaches ≥toad via re-collection and the published KEL passes the client verifier; keripy core is unchanged.

## Risks

1. **Receipting a duplicitous event.** *Mitigation:* the `db.kels.getLast` said-match guard — sign only the recorded first-seen version. This is stricter than canonical.
2. **Re-parsing the re-served receipt into `db.wigs`.** *Mitigation:* idempotent (`db.wigs.put` dedupes); the existing 200 path already does this re-parse (line 427).
3. **Extracting the serder from `ims` in the Lambda** (vs Falcon's `parseCesrHttpRequest`). *Mitigation:* `SerderKERI(raw=bytes(ims))` reads the leading sized event; the handler already relies on `framed=True` single-message POSTs. Cover with a unit test on the real CESR a client sends.
4. **Redeploy touches all 5 production witnesses.** *Mitigation:* the change is additive (only converts a `204` to a `200`+receipt for held events); deploy one stack first, validate with a re-POST, then roll the rest.
