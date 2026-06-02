# The KERI Stack as a DB Layer

## A developer's guide to deciding what goes into ACDCs vs. a regular database

If you come to KERI from web app development, the first instinct is to map ACDCs onto rows in a table. **Do not do that.** This document explains why, and gives you a working decision rule for when an event belongs in the KERI stack and when it belongs in your application's regular database.

It is industry-agnostic. Inventory shows up as a worked example, but the same shape applies to claims processing, supply-chain provenance, healthcare records, academic transcripts, ecosystem registries — anything where some events need verifiable provenance and most events do not.

---

## 1. The mental shift: KERI is not "the database"

The KERI stack — KELs, TELs, ACDCs, receipts — is **not a database in the relational sense**. It is closer to:

> "Kafka where every event is signed by a known issuer, schema-validated, content-addressed, and chained into a provenance graph."

It is a **cryptographically authenticatable, append-only event log with provenance**. Designed for the write side of a system where authenticity matters across organizational boundaries.

It is *not* designed to be:

- Queried like a SQL store (no `SELECT * WHERE qty < 10`).
- Mutated in place (every change is a new event).
- A free-form document store.
- The hot path of a high-volume read workload.

Trying to build a CRUD application on top of "an ACDC per row" will be miserable. Building an event-sourced application where some events are elevated to ACDC-grade feels native.

---

## 2. The KERI stack at a glance — what storage primitive does what

| Layer | What it is | What it stores |
|---|---|---|
| **KEL** (Key Event Log) | Per-AID signed sequence of key-state events | "Who is this AID, what are its current keys, has its authority been delegated, has it rotated?" |
| **TEL** (Transaction Event Log) | Per-registry signed sequence of lifecycle events | "Has this credential been issued? Revoked? Updated? Is it currently valid?" |
| **ACDC** | Authenticatable Chained Data Container — typed, schema-bound, signed payload | The actual claim or domain event, with edges to other ACDCs and a Ricardian rule section |
| **Witness receipts** | Signed acknowledgements of KEL events by witness AIDs | Third-party agreement that an AID's event ordering is what the controller says it is |
| **OOBIs** | Out-of-band introductions | Discovery — how to find another participant's KEL |

In keripy these all live in **LMDB**, accessed through structured sub-database wrappers (`Baser` for the core stores, `Suber` / `Komer` families for typed sub-DBs). The point: the KERI "database" is a real, on-disk thing — but what's *in* it is event-shaped, not row-shaped.

---

## 3. The lens that makes this click: **CQRS + Event Sourcing**

If you've seen Event Sourcing or CQRS before, this will feel familiar:

| ES/CQRS concept | KERI equivalent |
|---|---|
| Event log (Kafka, EventStore) | KEL + TEL + ACDC stream |
| Domain event | ACDC (when verifiability matters) or `exn` peer message |
| Authority to emit an event | AID's key state in the KEL, plus delegation chain |
| Schema | ACDC schema section (immutable, SAID-identified) |
| Projection / read model | Your application's regular database |
| Projector | A subscriber that folds the ACDC/TEL stream into your DB |

**The architectural rule of thumb:**

> The KERI stack is your **write side** — the durable, signed, cross-organizationally-verifiable record of what happened.
> Your application database is your **read side** — derived state, optimized for the queries your UI and business logic need.

State you can query fast belongs on the read side. Events you need to *prove* belong on the write side. They don't compete; they collaborate.

---

## 4. The decision rule: ACDC, or just a row?

Promote an event to ACDC-grade when **any** of these are true:

- A **third party who doesn't trust your DB** needs to verify it.
- It crosses an **organizational boundary** (supplier ↔ you, you ↔ customer, you ↔ regulator, you ↔ insurer, you ↔ partner ecosystem).
- It carries **delegated authority** that should chain (this action was valid because the actor holds credential X, issued by Y, who chains back to authority root Z).
- You need **non-repudiation** — the issuer cannot later deny it.
- You want a **legally durable audit trail** that survives database migrations, vendor changes, or the dissolution of the company.
- You will later present it under **selective disclosure** — prove a property without revealing every field.
- It needs to outlive your application.

Keep it as a plain DB row when:

- It's **internal-only and high-volume** (sensor reads, UI clicks, every individual warehouse pick scan, every page view).
- It's **transient or draft** state.
- It's on a **performance-critical hot path** where signature verification cost matters.
- Nobody outside the system will ever care about its provenance.
- It's **derived** state — a projection of other events.

This is per-event, not per-feature. A real KERI-native application will have **a small number of high-value events as ACDCs and a much larger volume of operational data as plain rows**. That ratio is healthy. If you find yourself wanting to put everything into ACDCs, you've slipped back into "database" thinking.

---

## 5. Worked example: inventory (apply the same pattern to your domain)

Imagine a warehouse system. The same shape generalizes — substitute "policy / claim" for an insurer, "transcript / course completion" for a school, "lot / batch" for a manufacturer.

### What goes into ACDCs (the verifiable events)

| ACDC type | Why it's ACDC-worthy |
|---|---|
| `EmployeeCredential` issued by HR | Carries delegated authority; appears as edge on every operational attestation |
| `WarehouseManagerCredential` chained from `EmployeeCredential` | Adds scope; chained authority |
| `InventoryCountAttestation` (Aggregate section over `[{sku, qty, location}, ...]`) | Auditor can later verify a single SKU count without disclosing the rest |
| `GoodsReceivedAttestation` with edge → PO ACDC + EmployeeCredential | Cross-org: supplier wants to verify; you want non-repudiation |
| `GoodsShippedAttestation` with edge → Order ACDC + EmployeeCredential | Cross-org: customer & carrier may verify |
| `WriteOffAttestation` requiring manager-tier credential on edge | Authority-bearing; legal audit trail; Rule section spells out the policy |

Each of these has a TEL so revocation/correction is a real lifecycle event, not a `DELETE`.

### What goes into plain DB tables (the projections)

```
inventory_levels(sku, location, qty, last_event_said)
open_orders(order_id, status, expected_ship_date, ...)
pick_queue(pick_id, sku, location, target_order, ...)
reorder_alerts(sku, current_qty, threshold, ...)
```

These are the **read side**. Computed by a projector that subscribes to the ACDC/TEL stream, validates each event's signatures and chains, and updates the table accordingly. The projector stores the SAID of the last applied event so it can resume.

### The wiring

1. A domain command arrives ("submit count").
2. The application service constructs an ACDC, signs it with the actor's AID, registers issuance in the TEL, anchors the TEL event in the controller's KEL.
3. A projector observes the new TEL event, fetches the ACDC, verifies signatures and edges, folds it into the projection tables.
4. UI reads come from projections.
5. Auditors verify directly against ACDCs — they don't need to trust your projections.

This is exactly the standard CQRS shape, with the event log replaced by something cryptographically authenticatable across organizational lines.

---

## 6. The thing that *will* trip you up: schema immutability

ACDC schemas are **immutable**. The schema's `$id` is a bare SAID, the schema is static, and "evolving" a schema means publishing a new schema with a new SAID.

Practical implications:

- You cannot `ALTER TABLE` an ACDC type. You issue a v2 schema and start using it.
- Old ACDCs remain valid against their original schema forever.
- Plan for schema versioning **before** you issue real ACDCs.
- Your projector must handle multiple schema versions of the same logical concept.

This is foreign to anyone used to running migrations and good to know on day one.

---

## 7. ACDC vs. `exn` — don't reach for a credential when a signed message will do

Not every signed cross-party communication should be an ACDC. KERI also has `exn` peer-to-peer messages, anchored by the sender's KEL, with no TEL lifecycle and no schema-as-credential semantics.

Use an **ACDC** when the artifact is a credential or a state-bearing claim with a lifecycle (issuance, revocation, expiry).

Use an **`exn`** when you just need a signed, attributed, ordered message between participants — quote requests, status pings, IPEX exchange traffic, application-level workflow messages.

A common pattern: an `exn` carries an ACDC as payload (e.g., presenting a credential to a verifier in IPEX). The wrapper is `exn`; the credential is ACDC. Each does what it's good at.

---

## 8. Where to actually put your projection tables

Two reasonable choices in a keripy app:

- **Same LMDB environment as the KERI stores**, in a sibling sub-database. Pros: one process, one transactional fate, embedded, no extra ops. Cons: hard to query from non-KERI tools.
- **Separate database** (Postgres, SQLite, DuckDB, Redis). Pros: queryable by anything; can join with non-KERI app data; supports analytics. Cons: now you have two storage systems to keep in sync.

Decision driver: **who else needs to query the projections?** Internal-only KERI services → embed in LMDB. BI dashboards, reports, joins with non-KERI data → separate DB.

Either way, **the ACDCs are still the source of truth.** A projection can be torn down and rebuilt by replaying. A lost ACDC cannot be recreated by replaying projections.

---

## 9. Anti-patterns to avoid

- **One ACDC per row.** Mapping a CRUD model onto ACDCs 1:1. The result is slow, expensive, and misses the point of provenance.
- **Querying ACDCs directly for application reads.** Build a projection. Read from it.
- **Editing ACDC schemas in place.** You can't. Plan versioning up front.
- **Putting high-volume internal telemetry in ACDCs.** If nobody outside the system will verify it, the cryptographic overhead is dead weight.
- **Skipping the TEL for state-bearing credentials.** Without a TEL, "is this credential still valid?" has no canonical answer.
- **Treating projections as authoritative.** They're derived. Auditors verify ACDCs.
- **Not recording the last-applied SAID in the projector.** Projectors must be resumable. Store the cursor.
- **Reaching for ACDCs when an `exn` is all you need.** Wrappers are cheaper than credentials.
- **Forgetting that authority is in the KEL.** An ACDC is only as trustworthy as the issuer's current key state. The KEL is where you check that.

---

## 10. Quick reference card

```
  Cross-org verifiability needed?  ─── yes ──┐
  Carries delegated authority?     ─── yes ──┤
  Non-repudiation required?        ─── yes ──┼──>  ACDC + TEL + KEL anchor
  Selective disclosure later?      ─── yes ──┤
  Survives the application?        ─── yes ──┘

  Internal hot-path event?         ─── yes ──┐
  High volume, low individual val? ─── yes ──┤
  Transient / draft state?         ─── yes ──┼──>  Plain DB row
  Derived from other events?       ─── yes ──┤
  Nobody outside ever verifies it? ─── yes ──┘

  Just a signed cross-party        ───────────>  exn message
  message with no lifecycle?                       (KEL-anchored, no TEL)
```

---

## 11. The one-line takeaway

> **The KERI stack stores the events you need to *prove*. Your regular database stores the state you need to *query*. Build both, keep them in sync via projectors, and don't let either swallow the other.**

Once that line is sharp, the rest of a KERI-native application's design falls out.
