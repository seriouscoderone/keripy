# Witness First-Seen via DynamoDB Concurrency (replacing `reserved_concurrency=1`) — Design

**Date:** 2026-06-19
**Status:** Approved (brainstorm) — ready for implementation plan
**Repo:** keripy fork (`~/code/keripy`), branch `feat/witness-ddb-first-seen`
**Backends touched:** the fork-only `src/keri/db/dynamodbing.py` (DynamoDBer) + a minimal, fork-localized routing signal in `src/keri/core/eventing.py` + the CDK witness stack.

## Summary

Replace the witness Lambda's `reserved_concurrent_executions = 1` (a process-level single-writer lock that kills availability and throughput) with **DynamoDB-native per-identifier concurrency control**: a conditional "first-seen claim" per `(pre, sn)` enforced inside the DynamoDBer. This recreates KERI's single-writer guarantee *in the database* — concurrent, horizontally-scalable, highly available — while preserving the only invariant the spec actually requires.

The guarantee lives in the fork's own storage abstraction and surfaces to keripy **only through an exception keripy already raises and handles** (`LikelyDuplicitousError`). keripy's decision logic and HIO event loop are unchanged; the sole new keripy touch is a minimal *routing* signal so validated recovery (superseding) can replace a first-seen claim that plain concurrent duplicates cannot.

### Locked decisions (from brainstorm, with two expert reviews)

1. **Substrate: stay serverless.** Keep Lambda witnesses; drop `reserved_concurrent_executions=1`. (Persistent-witness-on-Fargate was the considered alternative; rejected to preserve the CDK/oracle/scale-to-zero investment and pursue the user's thesis: DynamoDB concurrency replacing the single-writer cap.)
2. **Gate location: the DynamoDBer** (`dynamodbing.py`) — fork-only, so zero upstream divergence.
3. **Mechanism: conditional first-seen claim** per `(pre, sn)`, surfaced via keripy's existing `LikelyDuplicitousError`.
4. **Superseding: a distinct DynamoDBer "validated-supersede" primitive**, routed to by keripy's existing recovery branch (the one minimal `eventing.py` touch).
5. **Reads unguarded** (OOBI / `GET /receipts` / key-state); never make a duplicity decision via a GSI.
6. **DynamoDB mechanics (per the DDB architect review):** single conditional `PutItem`; **no `#HEAD` counter**, **no `TransactWriteItems`**, **no receipt regeneration**; reuse keripy's per-AID `fels` for `fn` (already conditional-safe); `ReturnValuesOnConditionCheckFailure: ALL_OLD` (+ boto3 version pin + a test that the conflicting item is parsed).
7. **Scope: witnesses first; Service-AID is a fast-follow** (same primitive); the mailbox is already uncapped and stays so.

## Background — what the experts established

Two parallel expert reviews informed this design.

**keripy ground truth** (`Kevery.processEvent` / `Kever` / `logEvent`):
- An accepted event triggers ~9 **non-atomic** sub-store writes (`dtss. sigs. wigs. wits. evts. esrs. fels. fons. kels.`). The digest-keyed stores (`evts./sigs./dtss./wits./esrs.`) are **idempotent re-puts** (same event ⇒ same write). `fn` is **per-AID**, assigned via `fels.append()` → `appendOnVal` (already made conditional-safe in a prior fix).
- `db.kels` is an **IoSet that deliberately holds multiple digests** at `(pre,sn)` (recovery/superseding records a second dig; `kels.getLast` is the accepted one).
- The race is narrow: **TOCTOU on the first-seen winner** — for an in-order event, two concurrent different-`said` events both pass keripy's checks and both `kels.add`, yielding undetected duplicity — **plus warm-instance in-memory `Kever` staleness**.
- keripy **already** raises `LikelyDuplicitousError` and routes losers to `ldes` escrow (`eventing.py:7278`, `escrowLDEvent`).

**DynamoDB architecture review:**
- The `attribute_not_exists` conditional put + `ReturnValuesOnConditionCheckFailure: ALL_OLD` is the correct, idiomatic primitive for first-seen-wins (append-once, immutable).
- A per-identifier monotonic `#HEAD` counter is a **non-raisable ~1,000 WCU/s single-item ceiling** — it recreates the bottleneck. `TransactWriteItems` adds 2× cost + conflict-retry storms + can't return updated values. **First-seen needs a total order, not a dense counter** — `(sn, said)` already provides it; keripy's `fn` is an audit ordinal, not consumed as a dense integer.
- "Ed25519 deterministic ⇒ regenerate the receipt" is only conditionally true (breaks on timestamped payload or key rotation) — don't rely on it.

## The invariant to preserve (from the KERI spec)

> **Serializable first-seen per `(AID, sn)`.** The check-then-witness — *"is there already a first-seen event at this `sn`? if not, accept + receipt; if a different one, discard as duplicity"* — must be atomic per identifier.

From the spec's Witnessing Policy / First-Seen / Duplicity (validation + KAWA):
- *"First verified version is witnessed — first-seen wins."* *"Log is append-only; later messages MUST NOT change existing entries (except valid superseding events)."* *"Inconsistent receipts (different event version at same location) MUST be discarded."*
- *"First seen, always seen, never unseen."*
- A witness that signs two different events at the same `sn` is itself faulty (the `F` in KAWA), breaking the immunity property ("at most one sufficient agreement").

Explicitly **not** required: serializing different AIDs, serializing reads, or a global/dense sequence.

## Architecture

```
controller POST event ──▶ witness Lambda (NO concurrency cap; many instances)
                              │  parse + verify (keripy Kevery)
                              ▼
        keripy accept path ──▶ DynamoDBer.claimFirstSeen(pre, sn, said, ...)   [the gate]
                              │      conditional PutItem: attribute_not_exists(first-seen slot)
                              ├─ win  ──────────▶ proceed: idempotent store fills + fn (fels) + receipt
                              ├─ same said ─────▶ idempotent: return existing, no side effects
                              └─ different said ─▶ raise LikelyDuplicitousError  ──▶ keripy's existing
                                                                                     escrow (ldes), unchanged
        keripy recovery path ▶ DynamoDBer.supersedeFirstSeen(pre, sn, said, ...) [validated replace]
        reads (OOBI/receipts/key-state) ─▶ unconditional, base-table strong reads (never GSI for duplicity)
```

### Components

**1. `DynamoDBer.claimFirstSeen(pre, sn, said, ...)` — the gate (new, in `dynamodbing.py`).**
A single conditional `PutItem` claiming the `(pre, sn)` first-seen slot for `said` (`ConditionExpression: attribute_not_exists(pk)`, `ReturnValuesOnConditionCheckFailure: ALL_OLD`). Outcomes:
- **Win** → first-seen; keripy proceeds with the rest of `logEvent` (idempotent digest-keyed puts; `fn` via `fels.append`; receipt).
- **Conflict, same `said`** (the returned `ALL_OLD` item's `said` == incoming) → **idempotent**: no side effects, signal "already first-seen" so keripy treats it as a benign re-delivery.
- **Conflict, different `said`** → **raise keripy's `LikelyDuplicitousError`** → keripy's existing handler escrows to `ldes`. *No new keripy decision logic.*

This claim is also the **stale-instance safety net**: a warm Lambda whose cached `Kever` lagged loses the claim and falls through the same idempotent/duplicity path. **Correctness never depends on in-memory freshness.**

**2. `DynamoDBer.supersedeFirstSeen(pre, sn, said, ...)` — validated replace (new).**
Called *only* by keripy's existing recovery branch (the validated `rot`/`drt`-supersedes-`ixn` path, Rules A/B/C already decided by Kevery). Performs the replace/append that the immutable claim forbids. The DynamoDBer cannot itself distinguish "validated recovery" from "concurrent duplicity" (that is a Kevery semantic decision), which is why this is a distinct entry point rather than transparent.

**3. Minimal `eventing.py` routing signal (the one fork-localized keripy touch).**
keripy's accept path is changed only to **route**, not to decide: the first-seen-accept branch calls `claimFirstSeen`; the validated-recovery branch calls `supersedeFirstSeen`. keripy's first-seen-vs-recovery *decision* is unchanged; the duplicity *outcome* still surfaces via the existing `LikelyDuplicitousError`. (`eventing.py` already carries a fork delta, so this is consistent with the fork's posture — no new upstream divergence in spirit.)

**4. CDK witness stack (`keri_cdk/witness_stack.py`).** Remove `reserved_concurrent_executions=1`. Reads and writes both run concurrently; the database is the serialization point per identifier.

**5. Witness handler liveness refresh (optional, `keri_cdk/handlers/witness`).** Not required for correctness (the gate guarantees it). To avoid escrow churn under sustained staleness, the handler MAY refresh the target AID's key state (a strongly-consistent base-table point read) before processing. Documented as a liveness optimization, not a correctness dependency.

## Data flow (the cases)

- **First-seen (happy path):** verify → `claimFirstSeen` wins → idempotent store fills + `fels.append` (fn) + receipt → return receipt. One conditional write on the hot path.
- **Idempotent re-delivery:** `claimFirstSeen` conflict, same `said` → return the already-stored receipt; no new `fn`, no second receipt.
- **Concurrent duplicity:** two different-`said` events at `(pre,sn)` → exactly one wins; the loser's `claimFirstSeen` raises `LikelyDuplicitousError` → escrowed to `ldes`, never receipted.
- **Cross-AID:** different `pre` (or different `sn`) → different `pk` → no contention, full concurrency.
- **Validated recovery (superseding):** Kevery validates Rules A/B/C → recovery branch calls `supersedeFirstSeen` → the claim is replaced; the prior first-seen remains observable per KERI recovery semantics.
- **Stale warm instance:** lagging `Kever` → loses `claimFirstSeen` → idempotent (same said) or escrow (different said); handler refresh (if enabled) prevents repeated churn.

## DynamoDB specifics (applying the architecture review)

- **One conditional `PutItem`** per accept; **no `TransactWriteItems`**, **no `#HEAD` counter**. `fn` reuses keripy's per-AID `fels.append` (already conditional-safe) — it is an audit ordinal, not a dense protocol counter.
- **`ReturnValuesOnConditionCheckFailure: ALL_OLD`** returns the conflicting item on the failed put — no second read. **Pin the boto3/botocore version and add an explicit test** that the conflicting item is parsed and the same-`said` vs different-`said` branch fires (a silent null here would misclassify every replay as duplicity — a quiet, high-impact bug).
- A failed conditional write still costs ~1 WCU (acceptable for replays/duplicates).
- **Never route the duplicity decision through a GSI** (GSIs are eventually consistent and forbid `ConsistentRead`). The claim is a base-table conditional put on the exact `pk`, strongly consistent by construction. Base-table `GetItem` may use `ConsistentRead=true` when read-after-write is needed.
- Partition key `"<pre>#<sn>"` spreads well; there is **no single hot item** (the `#HEAD` counter that would have been one is eliminated).

## Error handling — the failure taxonomy (containment guarantee)

The design's correctness rests on *where each failure lands*. The guarantee: **no failure mode can accept two conflicting events or lose a first-seen; the worst realistic case is a retry that converges. The only types reaching keripy are `LikelyDuplicitousError` (already handled) and the agreed supersede routing.**

1. **Conditional write loses the race — the mechanism, not an error.**
   - Same `said` → idempotent success inside the DynamoDBer; **surfaces nowhere**.
   - Different `said` → `LikelyDuplicitousError` → keripy's **existing** `ldes` escrow path. **No new surface.**
   - Stale in-memory `Kever` lands here too (loser → escrow → re-sync).
2. **Transient infra (throttle, contention) — absorbed inside the DynamoDBer.** A plain conditional put returns a clean `ConditionalCheckFailed` (no transaction ⇒ no `TransactionConflict` retry-storm). Genuine throttling is handled by boto3 adaptive retry + the existing bounded backoff (`_append_at_free_ion` discipline). Transient ⇒ never surfaces.
3. **Persistent/unrecoverable infra — surfaces only as the handler's existing `→ 500`.** Exhausted retries / sustained outage raise a plain exception → witness handler returns HTTP 500 → the controller re-submits (KAWA is at-least-once round-robin). This is the *one* non-typed surface, but it needs **no new keripy code** (the handler already maps any error to 500) and is a *transport* failure, never a *correctness* one.
4. **Partial multi-store write — keripy's native escrow + idempotency self-heals.** Claim succeeds but a later store write fails ⇒ incomplete accept. On the next processing (controller re-submit or escrow drain): digest-keyed stores re-put idempotently, the claim sees **same `said` → idempotent → completes**, and the witness re-adds its receipt (idempotent IoSet add) because the controller — lacking `toad` receipts — re-sends. **No new handling.** Ordering rule that makes this hold: **write the claim first** (the gate); treat everything after as idempotent fill.

**Deliberate, recorded trade-off:** with keripy's separate `db.wigs` store, an *accepted-but-not-yet-receipted* event heals via the controller's next round-robin re-send (not via a single atomic accept+receipt item, which keripy's multi-store model doesn't allow without restructuring). Spec-safe (KAWA re-sends until `toad`); documented so it is a known property, not a surprise.

## Scope

- **In scope:** the witness role — drop `reserved_concurrent_executions=1`, add `claimFirstSeen` + `supersedeFirstSeen` in the DynamoDBer, the minimal `eventing.py` routing, the optional handler refresh, and the test suite.
- **Fast-follow (separate effort):** the Service-AID Lambda (also `reserved_concurrent_executions=1`) — same DynamoDBer primitive; out of scope here to keep this plan single-purpose.
- **Untouched:** the mailbox (already uncapped; streaming fan-out). The oracle's `SHARED_KEL_STORES` narrowing (already shipped). Upstream keripy `eventing.py` decision logic (only routing is added).

## Testing

- **Acceptance (BDD/Gherkin):** first verified version accepted + receipted + assigned `fn`; same-event re-delivery idempotent (no second `fn`, no second receipt, returns prior receipt); different version at same `sn` = duplicity (escrowed, never receipted, both retained as evidence); concurrent conflicting writes ⇒ exactly one first-seen; different AIDs at same `sn` don't contend.
- **Real-AWS N-writer probe:** extend `keri_cdk/probes/concurrent-append/` to hammer the same `(pre, sn)` with N parallel invokers and assert exactly one first-seen winner + others classified duplicity — proving the **conditional write, not the cap**, enforces it. (moto cannot reproduce true concurrent conditional races; this needs real AWS.)
- **Unit (moto):** `claimFirstSeen` win/idempotent/duplicity classification incl. the `ALL_OLD` parse; `supersedeFirstSeen` replace; the boto3-version-pin parse test.
- **Regression:** the full `tests/db/` + witness/Kevery suites stay green (the routing change must not alter first-seen/recovery decisions).

## Out of scope

- Service-AID Lambda (fast-follow). Persistent-witness (Fargate) substrate (rejected branch). Mailbox changes. Full superseding *re-implementation* (we route to a validated-replace primitive; Kevery's Rules A/B/C decision is unchanged). Restructuring keripy's multi-store model into one atomic accept+receipt item.

## Definition of done

`reserved_concurrent_executions=1` removed from the witness stack; `claimFirstSeen`/`supersedeFirstSeen` enforce first-seen in the DynamoDBer; concurrency duplicity surfaces only via `LikelyDuplicitousError`; recovery still works via the routed supersede; reads run unguarded; the real-AWS N-writer probe proves exactly-one-first-seen under concurrency; full regression green; the failure taxonomy holds (no path accepts two conflicting events or loses a first-seen).

## Risks

1. **The `ALL_OLD` conflicting-item parse is SDK-fragile** — if it silently returns null, every idempotent replay is misclassified as duplicity. *Mitigation:* boto3 version pin + an explicit parse test (top of the test list).
2. **The `eventing.py` routing touch drifts from upstream.** *Mitigation:* keep it to pure routing (call `claimFirstSeen` vs `supersedeFirstSeen`), no decision changes; cover with the regression suite; document the delta.
3. **Superseding/recovery interaction is the subtle correctness frontier** — a mis-routed recovery would be wrongly rejected as duplicity, or a mis-routed duplicate wrongly allowed to supersede. *Mitigation:* the two primitives are explicitly distinct; recovery is exercised in the BDD suite (a `rot` superseding an `ixn` at the same `sn`), not just first-seen.
