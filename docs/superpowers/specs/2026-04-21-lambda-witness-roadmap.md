# Lambda KERI Witness — Roadmap

## Context

The serverless witness at `https://witness.keri.host` (stack: `serverless-witness`, us-east-1) is deployed and responding to basic status requests, backed by DynamoDB via the `lambding` setup functions. Cold-start initialization works, state persists across invocations, and 98 keripy tests pass. But the service is not yet usable by other KERI agents: its OOBI responses are JSON status blobs instead of signed CESR reply streams, the receipt-generation pipeline is wired but untested end-to-end, and the mailbox sub-databases are attached yet lack any HTTP retrieval surface.

This roadmap sequences the remaining work to make the Lambda stack a **spec-compliant witness + colocated mailbox**, the deployment pattern `kli witness` uses by default. Watcher and Registrar/TEL roles are explicitly deferred to separate future Lambda stacks.

## Scope

**Architectural target: witness + mailbox combo on one Lambda.** One AID serves both the witness role (receipt generation for controllers who list us in their KEL) and the mailbox role (asynchronous message delivery for intermittently-connected controllers). This mirrors the standard keripy deployment and keeps the existing DynamoDB schema intact — `MAILBOXER_STORES` is already attached to the Baser table.

**In scope (4 phases, interop-first ordering):**

| # | Phase | Role | Size | State |
|---|-------|------|------|-------|
| 1 | OOBI compliance | witness | Small | **Shipped** — `2026-04-21-lambda-witness-oobi-design.md` |
| 2 | Receipt generation polish | witness | Small | **Shipped** — `2026-05-05-lambda-witness-receipts-phase2-design.md` |
| 2.5 | kli/hio interop investigation | witness | TBD | **Open** — pytest 10/10 against the witness; `kli incept --receipt-endpoint` reports `Receipts: 0` despite witness returning a valid receipt. Suspected hio HTTPS + API Gateway interaction or `Receiptor.receipt()` body handling. Tracking issue, may need upstream keripy fix. |
| 3 | Mailbox read endpoints | mailbox | Medium | Write-only today; needs `GET /mailbox/{aid}`, pagination, message consumption |
| 4 | Init hardening | local | Small | (a) `_clear_keeper` workaround in place; replace with transactional init; (b) cold-start hangs to 120s timeout — observed ~3.7% rate over 246 invocations 2026-05-05/06 with zero Python output between START and timeout, suggesting silent boto3/DynamoDB stall; tighten botocore `connect_timeout`/`read_timeout`/`max_attempts` and add init() breadcrumb logging |

**Out of scope — future separate Lambda stacks:**

- **Watcher role** (`sam-watcher`) — key state observation across witnesses, duplicity detection. Uses `Adjudicator` (already in `src/keri/app/watching.py`) + new `/ksn` endpoint + polling framework. Must not colocate with witness for the same controllers (anti-collusion).
- **Registrar / TEL / ACDC credentials** (`sam-registrar`) — `setup_reger` + 34 new stores + `Tevery` wiring + credential issuance/revocation endpoints. Own lifecycle, own deployment.

## Dependency graph

```
Phase 1 (OOBI)  ── required by ──> all phases that expose new HTTP surfaces
                                    (nothing else works until agents can discover us)
Phase 2 (Receipts) ── depends on ── Phase 1 (agents OOBI-resolve before sending events)
Phase 3 (Mailbox) ── depends on ── Phase 1 (/oobi/{aid}/mailbox/{eid} discovery)
                ── benefits from ── Phase 2 (mailbox often stores witnessed events)
Phase 4 (Init hardening) ── independent ── can ship any time; lowest urgency
```

## Shared contract across phases

Every new HTTP surface added after Phase 1 MUST return **CESR bytes with the correct headers**, using the same pattern Phase 1 establishes:

```
Content-Type: application/cesr
(role-specific header, e.g. KERI-AID)
Body: base64-encoded CESR stream
isBase64Encoded: true
```

No more JSON pretending to be a KERI response. JSON is acceptable only for the human-facing `GET /` status endpoint and error responses.

Phase 3 (mailbox) will reuse the URL registered by Phase 1 — same `/loc/scheme` reply covers both witness and mailbox roles at the same AID. Mailbox role authorization gets added additively via `makeEndRole(eid=hab.pre, role=Roles.mailbox)` during its cold-start init; no schema change.

## Rationale for the sequencing

**Why OOBI first:** A KERI agent that cannot cryptographically verify our AID ↔ URL binding will refuse to trust anything we serve — receipts, mailbox deliveries, key state. OOBI is the entry point of the trust chain. Every downstream feature is dead weight without it.

**Why Receipts second:** The witness's protocol-level purpose is to endorse controller events. The code is already 90% wired (cue draining + `hab.receipt` call in `handle_cesr_ingest`), but it silently swallows exceptions and doesn't log failures. Small polish, high value.

**Phase 2 must-fix item discovered during Phase 1 conformance testing:** standard KERI HTTP clients (`kli`, `signify-ts`, `keria`) split CESR requests across the body and a `CESR-ATTACHMENT` header — the body holds the event Serder, the header holds counted signatures and other attachments (see `src/keri/app/httping.py:streamCESRRequests`). Our `handle_cesr_ingest` and `handle_receipt_post` only read the body, so they receive the event with no signatures, escrow it, and produce no receipts (returning HTTP 204). This makes the witness incompatible with every standard KERI controller despite working when CESR is constructed inline in the body. Phase 2 must concatenate `event["headers"].get("CESR-ATTACHMENT", "")` with the body before parsing.

**Why Mailbox third:** New HTTP surface area (GET + DELETE endpoints, pagination, optional filtering by timestamp). Stores are already attached. Benefits from Phase 1 (discovery) and Phase 2 (receipted events stored before forwarding).

**Why Init hardening last:** The `_clear_keeper` fallback works. Root-cause fix requires refactoring `Manager.incept` to be transactional — that's a change to keripy's own state machine, not Lambda-specific. Lowest urgency because we have no correctness bug today, just brittle recovery logic.

## Phase size estimates

These are for rough planning only — actual estimates happen inside each phase's design doc.

| Phase | LOC touched | New tests | Deploy impact |
|-------|-------------|-----------|---------------|
| 1 OOBI | ~100 handler + ~5 template | OOBI round-trip test | Template change (binary media types), one env var |
| 2 Receipts | ~30 handler | Receipt generation test | No infra change |
| 3 Mailbox | ~150 handler + 2-3 new routes | Mailbox CRUD tests | API Gateway routes added |
| 4 Init hardening | ~60 handler (delete workaround) + possible keripy patch | Init idempotency test | No infra change |

## Success criteria per phase

**Phase 1 done when:** A fresh local `Habery` can parse the CESR body returned by `https://witness.keri.host/oobi`, accept the witness's KEL, and store a `/loc/scheme` record with the correct URL. This proves third-party agents can bootstrap trust.

**Phase 2 done when:** A standard KERI controller using `streamCESRRequests` can POST its inception event in the body+`CESR-ATTACHMENT`-header format and receive back a CESR receipt signed by the witness, verifiable against the witness's KEL fetched via OOBI. Met by `test_post_receipts_kli_format` and `test_get_receipts_after_post` in `sam-witness/test_live.py`. The original "kli status --verbose reports `Receipts: 1`" criterion was deferred to Phase 2.5 — the witness produces and returns a valid receipt, but kli's hio-based HTTP client does not deliver it to `parseOne` for storage in alice's local `db.wigs`. The differential is the HTTP client (urllib works, hio does not).

**Phase 2.5 done when:** kli or any keripy-based controller using the standard Receiptor flow successfully stores a witness receipt against `https://witness.keri.host`. Likely requires either a fix to keripy's `keri.app.agenting.Receiptor` / `keri.app.httping.streamCESRRequests` (hio HTTPS port handling, body recovery) or upstream keripy issue. The bash `sam-witness/test_live.sh` already exercises this path; today it warns and continues, on Phase 2.5 completion the warn becomes a hard fail.

**Phase 3 done when:** A controller can `POST` a forward-addressed exchange message to the witness, then `GET /mailbox/{recipient-aid}` to retrieve it, with the stream containing the original signed message intact.

**Phase 4 done when:** (a) A Lambda cold start after simulated partial-init failure (pidx written, no signatory) succeeds without invoking `_clear_keeper`, and the `_clear_keeper` function is deleted. (b) Cold-start timeouts drop to <0.5% of invocations over a representative sample, with `init()` breadcrumb logs that pinpoint any remaining stall step.

## Open questions (resolve in each phase's design)

- **Phase 3 mailbox**: poll or long-poll? SQS bridge for push delivery? Deferred until that phase.
- **Phase 4 (a) signator**: is the root-cause fix a local `witness_handler.py` change or does it require a patch upstream to keripy's `Manager.incept`? Investigate during design.
- **Phase 4 (b) cold-start timeouts**: confirm the stall is in boto3 (most likely DynamoDBer.open or the first table call) by adding breadcrumb logs and reproducing. If boto3 retries are the cause, a tighter `botocore.config.Config` should be sufficient. If something else (e.g., LMDB-equivalent file ops, salt generation, a hanging `Configer` op) needs fixing, scope expands.
