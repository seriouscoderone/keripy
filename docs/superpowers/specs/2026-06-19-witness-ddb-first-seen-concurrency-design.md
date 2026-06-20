# Witness First-Seen via DynamoDB Concurrency (replacing `reserved_concurrency=1`) — Design

**Date:** 2026-06-19 (design revised 2026-06-20 — see Design Revision below)
**Status:** Approved (brainstorm) + implementation-grounded + layering-corrected — ready for implementation plan
**Repo:** keripy fork (`~/code/keripy`), branch `feat/witness-ddb-first-seen`
**Backends touched:** `src/keri/core/eventing.py` (the KERI-layer first-seen composition, fork-localized) + a one-line generic `singleWriter` flag on the fork-only `src/keri/db/dynamodbing.py` (DynamoDBer) + the CDK witness stack.

> ## Design Revision (2026-06-20) — keep the first-seen concept OUT of the storage layer
> Earlier drafts put `claimFirstSeen`/`supersedeFirstSeen` **methods on the `DynamoDBer`**. That leaks a KERI/witness concept ("first-seen, always seen") into a generic data-layer abstraction — wrong. **Corrected:** the storage layer exposes only the generic primitives it already has — `putVal` (conditional insert, `attribute_not_exists`, returns bool), `getVal` (strong point read), `setVal` (overwrite) — plus one generic concurrency-model flag, **`singleWriter`** (default `True`; the `DynamoDBer` sets `False`). The **KERI layer (`Kever`/`eventing.py`)** owns first-seen: a private `Kever._claimFirstSeen`/`_supersedeFirstSeen` **composes** those generic primitives over a `fseen.` marker store, gated by `if not getattr(self.db, "singleWriter", True)`. Wherever older phrasing below says "`claimFirstSeen` in the DynamoDBer," read it as "`Kever._claimFirstSeen` composing the storage layer's generic `putVal`/`getVal`." This removes the abstraction leak *and* dissolves the old `ALL_OLD`-parse risk (the incumbent is read with the generic `getVal` on the rare conflict), with less code.

## Summary

Replace the witness Lambda's `reserved_concurrent_executions = 1` (a process-level single-writer lock that kills availability and throughput) with **DynamoDB-native per-identifier concurrency control**: a conditional "first-seen claim" per `(pre, sn)`. This recreates KERI's single-writer guarantee *in the database* — concurrent, horizontally-scalable, highly available — while preserving the only invariant the spec actually requires.

The claim is composed in the **KERI layer** (`Kever`) from the storage layer's **generic** conditional-write primitive, and surfaces to the rest of keripy **only through an exception keripy already raises and handles** (`LikelyDuplicitousError`). keripy's decision logic and HIO event loop are unchanged. The storage layer learns nothing about "first-seen"; it only advertises whether it serializes concurrent writers (`singleWriter`), so the KERI layer knows when it must enforce first-seen itself (the LMDB/desktop path is single-writer and unaffected — byte-identical to upstream).

### Locked decisions (from brainstorm, two expert reviews, + the 2026-06-20 layering correction)

1. **Substrate: stay serverless.** Keep Lambda witnesses; drop `reserved_concurrent_executions=1`. (Persistent-witness-on-Fargate was the considered alternative; rejected to preserve the CDK/oracle/scale-to-zero investment and pursue the user's thesis: DynamoDB concurrency replacing the single-writer cap.)
2. **Gate location: the KERI layer (`Kever`/`eventing.py`).** The storage layer (`DynamoDBer`) exposes only generic primitives (`putVal`/`getVal`/`setVal`) + a generic `singleWriter` flag — no first-seen/witness concept leaks into it. `Kever` composes the claim and decides when to enforce it (`not self.db.singleWriter`). `singleWriter` defaults `True` via `getattr`, so upstream LMDB code and `dbing.py` are untouched — zero upstream divergence.
3. **Mechanism: conditional first-seen claim** per `(pre, sn)` via the generic `putVal` (one conditional `PutItem`), surfaced via keripy's existing `LikelyDuplicitousError`. On a lost claim the incumbent is read with the generic strong `getVal` (rare conflict path).
4. **Superseding: `Kever._supersedeFirstSeen`** (overwrite the marker via the generic `setVal`), routed to by keripy's existing recovery branch via an `is_supersede` flag on `logEvent`.
5. **Reads unguarded** (OOBI / `GET /receipts` / key-state); never make a duplicity decision via a GSI.
6. **DynamoDB mechanics (per the DDB architect review):** the claim is the existing generic `putVal` — a single conditional `PutItem` (`attribute_not_exists`); **no `#HEAD` counter**, **no `TransactWriteItems`**, **no receipt regeneration**; reuse keripy's per-AID `fels` for `fn`. The marker holds only `said`; the incumbent is read with `getVal` on conflict — **no `ALL_OLD` parse** (so the old SDK-fragility risk is gone).
7. **Scope: witnesses first; Service-AID is a fast-follow** (same flag + composition); the mailbox is already uncapped and stays so.

## Background — what the experts established

Two parallel expert reviews informed this design.

**keripy ground truth** (`Kevery.processEvent` / `Kever` / `logEvent`):
- An accepted event triggers ~9 **non-atomic** sub-store writes (`dtss. sigs. wigs. wits. evts. esrs. fels. fons. kels.`). The digest-keyed stores (`evts./sigs./dtss./wits./esrs.`) are **idempotent re-puts** (same event ⇒ same write). `fn` is **per-AID**, assigned via `fels.append()` → `appendOnVal` (already made conditional-safe in a prior fix).
- `db.kels` is an **IoSet that deliberately holds multiple digests** at `(pre,sn)` (recovery/superseding records a second dig; `kels.getLast` is the accepted one).
- The race is narrow: **TOCTOU on the first-seen winner** — for an in-order event, two concurrent different-`said` events both pass keripy's checks and both `kels.add`, yielding undetected duplicity — **plus warm-instance in-memory `Kever` staleness**.
- keripy **already** raises `LikelyDuplicitousError` and routes losers to `ldes` escrow (`eventing.py:4328`, `escrowLDEvent`).

**DynamoDB architecture review:**
- A conditional `attribute_not_exists` put is the correct, idiomatic primitive for first-seen-wins (append-once, immutable). keripy's existing `putVal` already *is* this primitive; on conflict, read the incumbent with the existing strong `getVal` (the conflict path is rare — duplicate/duplicity).
- A per-identifier monotonic `#HEAD` counter is a **non-raisable ~1,000 WCU/s single-item ceiling** — it recreates the bottleneck. `TransactWriteItems` adds 2× cost + conflict-retry storms. **First-seen needs a total order, not a dense counter** — `(sn, said)` already provides it; keripy's `fn` is an audit ordinal, not consumed as a dense integer.
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
   Kever.logEvent (first=True) ─ if not self.db.singleWriter:
                              │     Kever._claimFirstSeen(serder)   [KERI-layer composition]
                              │        won = db.putVal(fsdb, snKey(pre,sn), said)   [generic conditional insert]
                              ├─ won ───────────▶ proceed: fels.append (fn) + fons + kels + receipt
                              ├─ lost, same said ▶ existing = db.getVal(...) == said ⇒ first=False (idempotent)
                              └─ lost, diff said ▶ existing != said ⇒ raise LikelyDuplicitousError
                                                    ──▶ Kevery wrapper → existing escrowLDEvent (ldes), unchanged
   Kever.logEvent (supersede=True) ─ Kever._supersedeFirstSeen(serder): db.setVal(fsdb, snKey(pre,sn), said)
   reads (OOBI/receipts/key-state) ─▶ unconditional, base-table strong reads (never GSI for duplicity)
```

### Components

**1. Storage layer — generic primitives only (`DynamoDBer`, `dynamodbing.py`).**
No first-seen method. The DynamoDBer already provides `putVal(db, key, val) -> bool` (conditional insert, `attribute_not_exists`), `getVal(db, key) -> bytes|None` (strong point read), and `setVal(db, key, val)` (overwrite). The **only** addition is a generic class attribute `singleWriter = False` — "this backend does not serialize concurrent writers to a key; callers that need single-writer semantics must enforce them." (The base/LMDB backends are single-writer; the KERI layer reads the flag with `getattr(self.db, "singleWriter", True)`, so no upstream class is touched.)

**2. KERI layer — `Kever._claimFirstSeen` / `Kever._supersedeFirstSeen` (`eventing.py`).**
`_claimFirstSeen(serder) -> (won: bool, existing_said: bytes|None)`: `won = self.db.putVal(fsdb, snKey(pre,sn), said)`; if not won, `existing = self.db.getVal(fsdb, key)`. `_supersedeFirstSeen(serder)`: `self.db.setVal(fsdb, snKey(pre,sn), said)` (validated recovery overwrite). `fsdb` is the `fseen.` store handle (`self.db.env.open_db(b"fseen.")`), opened only inside the `not singleWriter` branch. The first-seen concept, the store name, the key scheme, and the won/same-said/different-said interpretation all live here, in the KERI layer.

**3. `Kever.logEvent` gate + `Kever.update` routing (the fork-localized `eventing.py` touch).**
At the head of `logEvent`'s `if first:` block, gated `if first and not getattr(self.db, "singleWriter", True)`: call `_supersedeFirstSeen` when `supersede=True`, else `_claimFirstSeen` and route — won → proceed; lost+same-said → `first = False` (idempotent, no second `fn`); lost+different-said → raise `LikelyDuplicitousError`. `Kever.update` computes `is_supersede = sner.num <= self.sner.num` (the comparison `rotate` already makes) and passes it as `logEvent(..., supersede=is_supersede)`. keripy's first-seen-vs-recovery *decision* is unchanged — this is pure routing of an outcome to keripy's existing handlers, no new decision logic, and a no-op on single-writer (LMDB) backends.

**4. `Kevery.processEvent` escrow wrappers.**
A `LikelyDuplicitousError` raised by the gate inside the accept path is routed into keripy's **existing** `escrowLDEvent` (`ldes`) + re-raised — byte-for-byte the existing duplicity branch (`4323–4328`). Wrap the acceptance-branch `kever.update(...)` (`4267`) and the fresh-inception `Kever(...)` construction. No new escrow logic, no new exception type. No-op for LMDB (the gate never raises there).

**5. CDK witness stack (`keri_cdk/witness_stack.py`).** Remove `reserved_concurrent_executions=1`. Reads and writes both run concurrently; the database is the serialization point per identifier.

**6. Witness handler liveness refresh (optional, `keri_cdk/handlers/witness`).** Not required for correctness (the gate guarantees it). To avoid escrow churn under sustained staleness, the handler MAY refresh the target AID's key state (a strongly-consistent base-table point read) before processing. Documented as a liveness optimization, not a correctness dependency.

## Implementation grounding (current keripy, branch `feat/witness-ddb-first-seen`)

These subsections resolve the implementation-time questions against the actual code so the plan inherits decisions instead of rediscovering them. Line anchors are `src/keri/core/eventing.py` unless noted; verified against this branch.

### 1. Where the claim is composed + the return contract

The single first-seen commit point is `Kever.logEvent` (3484–3569). Its `if first:` block assigns `fn` via `db.fels.append` (3551) and pins `db.fons` (3560); `db.kels.add` (3565) runs unconditionally as an idempotent IoSet add. Signatures are already verified before `logEvent` is reached (`valSigsWigsDel` — ixn path 2429–2438 → `logEvent` 2442; rot/drt path 2360–2371 → `logEvent` 2376). **So the claim belongs at the head of `logEvent`'s `first` path, gating `fels.append`/`fons.pin` — after signature verification, before the first-seen-defining writes.** The digest-keyed puts preceding it (`dtss`/`sigs`/`wigs`/`wits`/`evts`/`esrs`, 3519–3547) are `said`-keyed and idempotent, so a loser writing them is harmless evidence — the very writes `escrowLDEvent` itself makes (5619–5620).

`Kever._claimFirstSeen` **returns `(won, existing_said)`; it does not raise** — keripy-domain exceptions are raised by `logEvent`'s routing, not by the storage primitives (which only ever return values / `ConditionalCheckFailed`→`False`). `logEvent` routes:
- **won** → proceed (`fels.append`, `fons.pin`).
- **lost, same `said`** → idempotent: set `first = False` so the block skips `fels.append`/`fons.pin` (`fn` stays `None`), falling through to the idempotent `kels.add`; `logEvent` returns `(None, dts)` so the caller's `if fn is not None` guard (2399/2449) correctly skips re-pinning state. **This is the concurrency/stale-cache safety net, not the common idempotent re-delivery** — keripy already catches ordinary same-event re-delivery *earlier* via `db.kels.getLast` (icp 4218, non-icp 4300), routing it to `logEvent(first=False)` so it never reaches the claim. The claim's same-said branch fires only when that earlier check missed the slot (GSI lag / stale warm `Kever`).
- **lost, different `said`** → `logEvent` raises `LikelyDuplicitousError`. Because this originates inside the accept path (not keripy's duplicity `else`-branch at 4297–4328), the `kever.update` call in `Kevery.processEvent`'s acceptance branch (4267) is wrapped to route it into the **existing** `escrowLDEvent` (5595) + re-raise — byte-for-byte the behavior of 4323–4328. (The fresh-inception `Kever(...)` construction is wrapped the same way for the sn=0 case.)

**Motivation, grounded:** keripy's *existing* duplicity check reads `db.kels.getLast(keys=pre, on=sn)` (4299) — a GSI-served, **eventually-consistent** read in the DynamoDB backend. Under GSI lag two concurrent different-`said` events can both see an empty slot, both reach `update`/`logEvent`, and both `kels.add` → undetected duplicity. The strongly-consistent base-table conditional `putVal` is exactly the backstop that closes that TOCTOU window — this is *why* the claim must be a strong conditional write, not another `getLast`.

### 2. Exact insertion point + ordering

Single acceptance funnel: `Kevery.processEvent` (4258–4271) → `Kever.update` → `Kever.logEvent`. `logEvent(first=True)` is the only first-seen writer (callers: ixn 2442, rot/drt 2376; idempotent re-delivery calls it with `first=False` at 4236/4321, which must *not* claim — the gate is inside `if first:`). **Insertion = the head of `logEvent`'s `if first:` block, gating `fels.append` (3551)/`fons.pin` (3560).** The claim must precede the **first-seen-defining** writes (`fels.append`/`fons.pin`/`kels.add`); the preceding `said`-keyed digest puts are idempotent and order-immaterial. The partial-write self-heal in §Error-handling rests on exactly this: claim wins → idempotent fills + `fn` + receipt; any later failure re-processes with the claim now same-`said` → idempotent completion.

### 3. Recovery reads & consistency

The superseding-recovery *decision* is keripy's, unchanged: `Kevery.processEvent` routes a recovery `rot`/`drt` (`kever.lastEst.s < sn <= sno`, 4258–4262 — off **in-memory** `Kever` state, not a GSI read) into `Kever.rotate`, whose recovery branch (2482–2521) validates the chain by reading the **prior** event (`psn = sn-1`): `pdig = db.kels.getLast(keys=pre, on=psn)` (2505) then `db.evts.get((pre, pdig))` (2511).

**Grounded fact:** `db.kels.getLast` resolves through `OnIoDupSuber.getLast` → `_query_gsi` (`dynamodbing.py` ~482) → **eventually consistent** (DynamoDB forbids `ConsistentRead` on a GSI). The follow-on `db.evts.get` (2511) is a strong base-table point read.

**Decision: do NOT add a strong-consistency read to the recovery prior-event lookup.** Under GSI lag it returns `None`/stale → `ValidationError` → keripy re-escrows (out-of-order) and retries — a *delay*, never a false-accept, consistent with the documented DynamoDB consistency profile (GSI-served ordered reads only delay acceptance). The existing escrow-retry is sufficient.

**Concurrent recovery.** `_supersedeFirstSeen` overwrites the marker with the generic `setVal`. Two concurrent *valid* recoveries at one `(pre, sn)` would require the controller to sign two different rotations at the same `sn` — controller-side duplicity, outside this layer's remit and handled by KERI the same as any duplicity. The same valid recovery arriving concurrently converges (idempotent overwrite of the same `said`). The test suite still includes a concurrent same-said recovery case to prove convergence. The first-seen-vs-recovery decision stays keripy's (the in-memory `sn` comparison `sner.num <= self.sner.num`); `update` passes it to `logEvent` as `is_supersede`. That flag + the §1 `escrowLDEvent` wrapper are the complete `eventing.py` routing surface.

## Data flow (the cases)

- **First-seen (happy path):** verify → `_claimFirstSeen` → `putVal` wins → idempotent store fills + `fels.append` (fn) + receipt. One conditional write on the hot path.
- **Idempotent re-delivery:** keripy's `kels.getLast` check catches it first (`logEvent(first=False)`, no claim). If a stale instance misses it, `putVal` loses + `getVal` returns same `said` → `first=False` → no new `fn`, no second receipt.
- **Concurrent duplicity:** two different-`said` events at `(pre,sn)` → exactly one `putVal` wins; the loser's `getVal` returns a different `said` → `logEvent` raises `LikelyDuplicitousError` → escrowed to `ldes`, never receipted.
- **Cross-AID:** different `pre` (or different `sn`) → different key → no contention, full concurrency.
- **Validated recovery (superseding):** Kevery validates Rules A/B/C → `update` sets `is_supersede` → `logEvent` calls `_supersedeFirstSeen` (`setVal`) → the marker is replaced; the prior first-seen remains observable in the `kels` IoSet per KERI recovery semantics.
- **Stale warm instance:** lagging `Kever` → loses `putVal` → idempotent (same said) or escrow (different said); handler refresh (if enabled) prevents repeated churn.

## DynamoDB specifics (applying the architecture review)

- **One conditional `PutItem`** (`putVal`) per accept; **no `TransactWriteItems`**, **no `#HEAD` counter**. `fn` reuses keripy's per-AID `fels.append` — an audit ordinal, not a dense protocol counter.
- **No `ALL_OLD`.** The incumbent is read with the generic strong `getVal` on the rare conflict path (a failed conditional put still costs ~1 WCU; a conflict read is one more strong point read — both acceptable for replays/duplicates). This is strictly simpler and removes the SDK-fragility the earlier `ALL_OLD` parse carried.
- **Never route the duplicity decision through a GSI** (GSIs are eventually consistent and forbid `ConsistentRead`). `putVal` is a base-table conditional put on the exact key, strongly consistent by construction; `getVal` is a strong base-table point read.
- The `fseen.` marker key (`snKey(pre,sn)`) spreads well; there is **no single hot item** (the `#HEAD` counter that would have been one is eliminated).

## Error handling — the failure taxonomy (containment guarantee)

The design's correctness rests on *where each failure lands*. The guarantee: **no failure mode can accept two conflicting events or lose a first-seen; the worst realistic case is a retry that converges. The only types reaching keripy's existing handlers are `LikelyDuplicitousError` (already handled) and the agreed supersede routing.**

1. **Conditional `putVal` loses the race — the mechanism, not an error.**
   - Same `said` → idempotent (`first=False`); **surfaces nowhere** beyond a benign return.
   - Different `said` → `logEvent` raises `LikelyDuplicitousError` → keripy's **existing** `ldes` escrow path (via the §4 wrapper). **No new surface.**
   - Stale in-memory `Kever` lands here too (loser → escrow → re-sync).
2. **Transient infra (throttle, contention) — absorbed in the storage layer.** A plain conditional put returns a clean `ConditionalCheckFailed` (no transaction ⇒ no `TransactionConflict` retry-storm). Genuine throttling is handled by boto3 adaptive retry. Transient ⇒ never surfaces.
3. **Persistent/unrecoverable infra — surfaces only as the handler's existing `→ 500`.** Exhausted retries / sustained outage raise a plain exception → witness handler returns HTTP 500 → the controller re-submits (KAWA is at-least-once round-robin). This needs **no new keripy code** and is a *transport* failure, never a *correctness* one.
4. **Partial multi-store write — keripy's native escrow + idempotency self-heals.** Claim succeeds but a later store write fails ⇒ incomplete accept. On the next processing (controller re-submit or escrow drain): digest-keyed stores re-put idempotently, the claim sees **same `said` → idempotent → completes**, and the witness re-adds its receipt (idempotent IoSet add) because the controller — lacking `toad` receipts — re-sends. **No new handling.** Ordering rule that makes this hold: **the claim gates the first-seen-defining writes** (`fels.append`/`fons.pin`/`kels.add`; the preceding `said`-keyed digest puts are idempotent — see §Implementation grounding §2); treat everything after the claim as idempotent fill.

**Deliberate, recorded trade-off:** with keripy's separate `db.wigs` store, an *accepted-but-not-yet-receipted* event heals via the controller's next round-robin re-send (not a single atomic accept+receipt item, which keripy's multi-store model doesn't allow without restructuring). Spec-safe (KAWA re-sends until `toad`); documented so it is a known property, not a surprise.

## Scope

- **In scope:** the witness role — drop `reserved_concurrent_executions=1`; add `singleWriter=False` + register the `fseen.` store; add `Kever._claimFirstSeen`/`_supersedeFirstSeen` + the `logEvent`/`update`/`processEvent` routing; the optional handler refresh; the test suite.
- **Fast-follow (separate effort):** the Service-AID Lambda (also `reserved_concurrent_executions=1`) — same flag + composition; out of scope here to keep this plan single-purpose.
- **Untouched:** the mailbox (already uncapped; streaming fan-out). The oracle's `SHARED_KEL_STORES` narrowing (already shipped). Upstream keripy `eventing.py` decision logic (only routing is added) and `dbing.py`/LMDB behavior (the gate is a no-op when `singleWriter` defaults True).

## Testing

- **Unit (moto):** the `singleWriter` flag (DynamoDBer is `False`, default `True` elsewhere); `fseen.` registered in `BASER_STORES` and **not** in `SHARED_KEL_STORES`. The generic `putVal`/`getVal`/`setVal` are already covered by the existing DynamoDBer suite.
- **Acceptance (over a moto DynamoDBer-backed Kevery):** first verified version accepted + assigned `fn` + marker set; same-event re-delivery idempotent (no second `fn`); different version at same `sn` = duplicity (escrowed to `ldes`, never receipted); validated recovery replaces the marker; different AIDs at same `sn` don't contend. Plus the LMDB regression (`singleWriter` defaults True → byte-identical, gate is a no-op).
- **Real-AWS N-writer probe:** N parallel invokers hammer the same `(pre, sn)` slot with distinct `said`s via the generic `putVal` and assert exactly one winner + the rest observe the single winning `said` — proving the **conditional write, not the cap**, enforces it; plus a same-`said` storm (all idempotent) and a same-`said` concurrent-recovery storm (converges). (moto cannot reproduce true concurrent conditional races; this needs real AWS. Operator-run, not CI.)
- **Regression:** the full `tests/db/` + `tests/core/test_eventing.py` + witness/Kevery suites stay green (the routing change must not alter first-seen/recovery decisions on either backend).

## Out of scope

- Service-AID Lambda (fast-follow). Persistent-witness (Fargate) substrate (rejected branch). Mailbox changes. Full superseding *re-implementation* (we overwrite the marker on a Kevery-validated recovery; Kevery's Rules A/B/C decision is unchanged). Restructuring keripy's multi-store model into one atomic accept+receipt item. Touching `dbing.py`/LMDB.

## Definition of done

`reserved_concurrent_executions=1` removed from the witness stack; the DynamoDBer advertises `singleWriter=False` and registers the `fseen.` store (not shared); `Kever._claimFirstSeen`/`_supersedeFirstSeen` enforce first-seen in the KERI layer over the generic storage primitives; concurrency duplicity surfaces only via `LikelyDuplicitousError` → existing `ldes` escrow; recovery still works via the routed `setVal`; reads run unguarded; LMDB behavior is byte-identical; the real-AWS N-writer probe proves exactly-one-first-seen under concurrency; full regression green; the failure taxonomy holds (no path accepts two conflicting events or loses a first-seen).

## Risks

1. **The `eventing.py` routing touch drifts from upstream.** *Mitigation:* keep it to pure routing (gate → `_claimFirstSeen`/`_supersedeFirstSeen`), no decision changes; the gate is a no-op when `singleWriter` defaults True so upstream LMDB behavior is unchanged; cover with the full `tests/core/test_eventing.py` regression; document the delta.
2. **Superseding/recovery interaction is the subtle correctness frontier** — a mis-routed recovery would be wrongly rejected as duplicity, or a mis-routed duplicate wrongly allowed to supersede. *Mitigation:* `_claimFirstSeen` (conditional insert) and `_supersedeFirstSeen` (overwrite) are explicitly distinct entry points selected by the `is_supersede` flag; recovery is exercised in the acceptance suite **and** the real-AWS probe including a concurrent same-said recovery racing the same slot, not just sequential first-seen. The recovery decision's prior-event read is eventually-consistent (GSI) but self-healing by re-escrow — see §Implementation grounding §3.
3. **A backend that needs the gate but doesn't set `singleWriter=False`** would silently skip first-seen enforcement. *Mitigation:* the default is the safe direction for the single-writer LMDB backend (skip is correct there); only a *new* concurrent backend could trip this, and the unit test pins `DynamoDBer.singleWriter is False`. Document the flag's contract on the attribute.
