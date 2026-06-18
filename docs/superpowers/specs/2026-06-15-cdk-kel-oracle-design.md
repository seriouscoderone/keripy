# Shared-KEL Key-State Oracle (DynamoDBer per-store namespace routing) — Design

> ⚠️ **AMENDED 2026-06-18 (SAM→CDK cutover).** This design pooled the full KEL — including the
> per-witness receipt/event **write-logs** (`evts. sigs. wigs. rcts. vrcs. fels. fons. dtss. wits.
> aess.`) — into the shared namespace. That broke witness-receipt toad convergence: keripy's
> `agenting.Receiptor` needs each witness to OWN its `wigs.`, but pooling made the N witnesses one
> writer (last-writer-wins) and only one receipt survived, so clients could not reach toad 3-of-5
> (proven live: `shared#evts=5` but `shared#wigs=1`). `SHARED_KEL_STORES` was **narrowed to key-STATE
> + reachability only** (`kels. stts. ksns. knas. ends. locs. eans.`); the write-logs stay
> per-witness. The oracle's contract is now "read a peer's current **key-state**", not "replay a
> peer's full KEL." See `docs/superpowers/specs/2026-06-18-sam-to-cdk-federation-cutover-design.md`
> and commit `327fecdd`.

**Date:** 2026-06-15
**Status:** Approved (brainstorm) — IMPLEMENTED, then AMENDED 2026-06-18 (see banner above)
**Repo:** keripy fork. Build runs as a **fresh subagent-driven session** in a worktree off `development`.
**Predecessor:** CDK Phase C (`e2dfe9ad`) shipped per-service isolation and explicitly deferred this oracle.

## Goal

Turn the shared `keri-core` table into a trust-domain **key-state oracle**: the public KEL /
receipt / key-state stores live in ONE shared namespace readable + writable by every service
(witness, mailbox, Service-AID), so any AID resolved/verified by any service is instantly
available to all — no re-OOBI, no re-fetch from witnesses, and a single consistent first-seen
view across the domain. Private per-node state (hab registry, escrows, KRAM, OOBI queues) and the
entire `Reger` (TEL + credential bodies) stay in each service's own namespace.

## Why this is sound (verified, see memory `project_kel_public_shared_oracle`)

KELs are public (ambient verifiability); KERI assumes untrusted storage (a shared/corrupted store
can only DoS, never cause forged-event acceptance). **First-seen = whatever is written, in write
order** — so a shared store cleanly makes "first-seen" the trust domain's *collective* first-seen.
This *improves* intra-domain consistency: with separate per-service KELs, two services could
first-see different events for one AID and disagree on its key state; a shared KEL gives one
canonical view, and a conflicting (duplicitous) event arriving at any service is detected against
that shared state and escrowed **privately**. keripy's own `Baser.clean()` (`basing.py:1534-1537`)
already encodes the regenerate-KEL / copy-state / **omit-escrows** split this design formalizes.

## Mechanism — per-store namespace routing (the ~10-line core)

`src/keri/db/dynamodbing.py` has a single key-formation chokepoint, `_nskey(name)` (`:346-352`):
`_pk`, `_gsi_pk`, `_query_gsi`, `_put_meta`/`_get_meta`, `_clear_store`, and version all funnel
through it. Add two optional params to `__init__` (`:196`) and `open` (`:224`):
`shared_namespace: str | None = None`, `shared_stores: set[str] | None = None`. Then:

```python
def _nskey(self, name: str) -> str:
    ns = (self._shared_namespace
          if self._shared_namespace and name in self._shared_stores
          else self.namespace)
    return f"{ns}#{name}"
```

`name` here is the subdb/store name (e.g. `"kels."`). **Backward compatible:** both params default
off ⇒ existing single-namespace opens (Phase C witness/mailbox/Service-AID, all current tests)
are byte-identical. No other `dynamodbing.py` method changes. (Validation must confirm `__meta__`
handling: a shared store's meta row PK becomes `__meta__#shared#<store>` and its `gsi_sk`
`shared#<store>`; the per-instance version meta — store name `"__meta__"`, not in `shared_stores`
— stays in the per-service namespace.)

## Store classification (security-critical; pinned in `keri_cdk` as `SHARED_KEL_STORES`)

**Shared (`shared` namespace) — verifiable key-event / receipt / key-state, all AID-prefix-keyed:**
`evts. fels. kels. dtss. sigs. wigs. rcts. vrcs. aess. fons. wits. stts. ksns. knas.`

**Private (per-service namespace) — everything else:**
- node hab registry: `habs. names. hbys.`
- **all escrows** (incl. unverified-receipt escrows): `pses. pwes. ooes. udes. ldes. ures. vres.
  pdes. uwes. mfes. dees. gpse. gdee. gdwe. epse. epsd. dpwe. dune. dpub. …`
- KRAM / challenge: `ctyp. msgc. … chas. reps. wwas. obvs.`
- OOBI queues + reply/endpoint config: `oobis. eoobi. coobi. roobi. woobi. moobi. rpys. rpes.
  eans. lans. ends. locs.`
- the entire **`Reger`** (TEL events + credential bodies + indices)

The shared set is deliberately the unambiguous public-key-state core. **Endpoint/OOBI sharing is
a v2 expansion** — trivially additive (append a store name to `SHARED_KEL_STORES`); out of scope here.

The shared-store set is defined ONCE as `SHARED_KEL_STORES` in `src/keri/app/lambding.py`, right
beside `BASER_STORES`/`REGER_STORES` (a pure constant — no behavior change to keripy core). It is a
strict subset of `BASER_STORES` (a unit test asserts `SHARED_KEL_STORES ⊆ set(BASER_STORES)` and that
it is disjoint from the escrow/`Reger` stores), and every handler imports it from there.

## Service rewiring

- **Witness / Mailbox handlers:** the Baser open passes `shared_namespace="shared",
  shared_stores=SHARED_KEL_STORES`. Private fallback namespace stays the Phase C value
  (`<stack-name>:kel` / `<stack-name>:mbx`). Mailbox's `Mailboxer` stores (`tpcs. msgs.`) are NOT
  in the shared set ⇒ stay private.
- **Service-AID:** the `db` (Baser) open adds `shared_namespace="shared",
  shared_stores=SHARED_KEL_STORES`; private fallback stays `<alias>:kel`. The `reger` open is
  **unchanged / fully private** (`<alias>:tel`, no shared args) — credential bodies never shared.
- **`KeriCoreStack` + `keri_host` app:** unchanged (already the shared table).

## IAM — one statement, union of four LeadingKeys patterns

Each pooled service's Lambda role table policy:
`dynamodb:LeadingKeys ForAllValues:StringLike = ["shared#*", "__meta__#shared#*", "{id}:*#*",
"__meta__#{id}:*"]` where `{id}` = `Aws.STACK_NAME` (witness/mailbox) or `alias` (Service-AID).
A single DynamoDB op touches one store ⇒ one namespace ⇒ matches one subset, so `ForAllValues`
holds. This replaces Phase C's single-grant statement in `witness_stack.py`/`mailbox_stack.py`
and broadens `service_aid.py`'s grant.

## Edge cases & error handling

- **Concurrent cross-service appends** to a shared store: Phase A's conditional-put + local-increment
  retry (`appendOnVal`/`addIoSetVal`, shipped `b2255ff1`) already makes appends race-safe across
  *any* writers (it is not service-aware).
- **Double first-seen of the same legit event** (two services accept it in one race window): benign
  — replay emits it twice, receivers process idempotently; narrowed further by the strongly-consistent
  "already accepted?" base-table read. **Validation item, not a code carve-out** (see Testing).
- **Duplicity** (different events at same sn): detected against the shared KEL; the loser is escrowed
  in the detecting node's **private** `ldes.` — correct and domain-consistent.
- **Shared meta rows** are written/read in the `shared` namespace; concurrent idempotent meta writes
  on open are fine (same keripy version/flags).

## Testing & validation

- **Unit (`tests/db/test_dynamodbing_namespace.py`):** `_nskey` routes a shared store →
  `shared#…` and a private store → `<ns>#…` within ONE DynamoDBer instance on one moto table;
  backward-compat (no shared args ⇒ all keys in `<ns>`); meta-row routing for a shared store.
- **Oracle test:** service A (one DynamoDBer, namespace `A`, shared on) writes an AID's KEL into a
  shared store; a *separate* service-B instance (namespace `B`, shared on) **reads that AID's
  key-event back from `shared`** — and canNOT see A's private-store rows.
- **Real-AWS (build session):** deploy `keri_host` with oracle on; assert witness-written KELs are
  readable by a second service from `shared`; **concurrent cross-service acceptance → replay/clone
  stays clean** (the double-FEL benign-ness check); re-run the LeadingKeys probe for the **two-grant**
  boundary (role allowed `shared` + own private, denied another service's private). Then tear down.

## Out of scope / deferred
- Endpoint/OOBI store sharing (v2 — additive).
- Any change to keripy core `basing.py`/`Habery` (the `Habery(db=...)` seam is sufficient).
- The build itself: a fresh subagent-driven session (clean context + adversarial review for
  `dynamodbing.py`, the repo's most correctness-sensitive file).

## Merge strategy
Build session: worktree + branch off `development`, subagent-driven, direct merge to `development`
(matches Phase A/B/C). This spec + its plan land on `development` now (docs only).
