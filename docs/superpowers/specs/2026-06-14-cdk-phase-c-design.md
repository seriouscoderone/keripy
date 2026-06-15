# CDK Phase C: Consolidate All KERI Services onto One Core Table (per-service isolation) — Design

**Date:** 2026-06-14 (revised 2026-06-15 — settled on the proven isolation model; shared-KEL oracle deferred)
**Status:** Approved (brainstorm complete; ready for implementation plan)
**Repo:** keripy fork, branch `feat/cdk-phase-c` (off `development`)
**Predecessors:** Phase A (DynamoDB concurrent-append hardening, `b2255ff1`); Phase B (SAM→CDK `keri_cdk` library, `a9d11b54`)

## Goal

Put every KERI service in a trust domain — witness, mailbox, and every Service-AID — on **one**
`KeriCoreStack` DynamoDB table, each in its **own** `dynamodb:LeadingKeys`-isolated namespace.
This is the Service-AID multi-tenant pattern Phase B already validated on real AWS, extended to
the witness and mailbox. It removes the schema-identical duplicate per-service tables and improves
durability, while keeping every service cryptographically and operationally isolated. The
**shared-KEL "key-state oracle" is deliberately deferred** to a focused follow-on (see below).

## Why (the driver) + why isolation, not sharing (yet)

**Cleanliness:** the per-service tables have identical schema/purpose; duplicating them inside one
operator's account/region/ecosystem isolates nothing and is confusing. One table per trust domain
(separate operators already get separate `KeriCoreStack`s via the distributable library) + better
durability (the core table has RETAIN + deletion/termination protection + PITR; per-service tables
have none).

**Why per-service isolation rather than a shared KEL pool — settled by research (2026-06-15):**

- **keria, the production multi-tenant KERI system, deliberately chose strict per-tenant
  isolation** — one `Habery`+`Baser`+`Keeper` per controller AID, no cross-tenant KEL sharing, no
  shared cache (`keria/src/keria/app/agenting.py:141-165,253-333`; the only shared store is routing
  metadata, `keria/src/keria/db/basing.py:29-103`). It is the battle-tested model.
- **The shared-KEL oracle is genuinely novel** — there is no prior art to copy, and it would
  require a new `DynamoDBer` capability (per-store namespace routing); see "Deferred" below.
- **YAGNI:** the oracle's payoff (services skipping counterparty re-resolution) only materializes
  once real verifying Service-AIDs exist — none are deployed yet.
- **B → A is additive, not a rework:** the oracle layers a per-store split on top of this same
  one-table foundation; KEL is regenerable (keripy `Baser.clean()` replays it), and the oracle is
  best built *before* the real production cutover, so there is no data migration either way.

## Research foundation (verified)

Confirmed against the KERI/ACDC specs, the keri.host RAG, and the keripy/keria/signify codebases:

- **KELs are public** (ambient verifiability); **KERI assumes untrusted storage** — a corrupted/
  shared store can only DoS, never cause acceptance of a forged event. (This is *why* a future
  shared KEL pool is safe — but see "Deferred" for why it isn't this phase.)
- **The only confidential data is the ACDC attribute body**, stored in keripy's `Reger.creds.`
  (`vdr/eventing.py:2400,2560`); the Baser/KEL holds **zero** bodies; the TEL holds only digests.
  Because each service's Reger lives in that service's own namespace here, **credential-body
  confidentiality is automatic** under per-service isolation — no special handling needed.
- **A verifier verify-and-discards** (the current Service-AID already does:
  `handlers/serviceaid/handler.py:157`, `credentials=[]`). **Witness/mailbox have no Reger.**
- **The real secret is key material in the keeper** (Secrets Manager), never in this table.

## The boundary rule

One `KeriCoreStack` table per trust domain. Within it, **each service gets its own namespace**,
`LeadingKeys`-locked so a service can touch only its own rows. No cross-service sharing. Separate
trust domains → separate tables (already enforced by the per-deployment library model).

## Identity & namespace convention

The **stack name** is the isolation unit for whole-stack services (already the keeper key:
`keri/{Aws.STACK_NAME}/keeper`). The Service-AID keeps its `alias` (it's a `Construct`, possibly
many per stack; `alias`-vs-stack-name collapse is deferred — memory `project_service_aid_alias_vs_stackname`).

| Service | namespace(s) | keeper | LeadingKeys |
|---|---|---|---|
| Witness | `<stack-name>:kel` | `keri/<stack-name>/keeper` | `<stack-name>:*#*`, `__meta__#<stack-name>:*` |
| Mailbox | `<stack-name>:mbx` | `keri/<stack-name>/keeper` | `<stack-name>:*#*`, `__meta__#<stack-name>:*` |
| Watcher (future) | `<stack-name>:kel` | `keri/<stack-name>/keeper` | `<stack-name>:*#*`, `__meta__#<stack-name>:*` |
| Service-AID | `<alias>:kel` + `<alias>:tel` **(unchanged)** | `keri/<alias>/keeper` | `<alias>:*#*`, `__meta__#<alias>:*` **(unchanged)** |

Keys are `{namespace}#{subdb}#{hex(key)}` (`dynamodbing.py:354`); GSI partition `{namespace}#{subdb}`.
This is byte-for-byte the layout the LeadingKeys probe verified on real AWS. CFN stack names and the
alias are `[A-Za-z0-9-]` (no `:`/`#`), so `<id>:kel` carries exactly the one colon we add and matches
`<id>:*#*` cleanly. **AID identity = keeper salt/bran, not the namespace**, so repointing the table
never changes an AID.

## Component changes (file by file)

### `keri_cdk/witness_stack.py`
- **Remove** the self-owned `ddb.Table` (`:70-82`). Add required `core_table: ddb.ITable`
  (cross-stack ref emits the CFN Export/`Fn::ImportValue` lifecycle lock).
- **Replace** `self.baser.grant_read_write_data(self.fn)` (`:121`) with the LeadingKeys-scoped
  policy mirroring `service_aid.py:202-225`: actions `DescribeTable/GetItem/PutItem/DeleteItem/Query/
  BatchWriteItem` over `core_table.table_arn` + `/index/*`, conditioned on `ForAllValues:StringLike`
  → `dynamodb:LeadingKeys: ["{Aws.STACK_NAME}:*#*", "__meta__#{Aws.STACK_NAME}:*"]`.
- **Env:** `WITNESS_BASER_TABLE = core_table.table_name`; add `WITNESS_NAMESPACE = f"{Aws.STACK_NAME}:kel"`.
- Keep `reserved_concurrent_executions=1`. Drop the misleading `WitnessBaserTableName` CfnOutput; add `WitnessNamespace`.

### `keri_cdk/mailbox_stack.py`
- Same shape: `core_table` param, remove own table (`:100-112`), swap `grant_read_write_data` (`:164`)
  for the LeadingKeys policy, env `MAILBOX_BASER_TABLE = core_table.table_name` +
  `MAILBOX_NAMESPACE = f"{Aws.STACK_NAME}:mbx"`. Keep no reserved-concurrency, both layers,
  `ResponseTransferMode.STREAM`. Drop `MailboxBaserTableName` output; add `MailboxNamespace`.

### `keri_cdk/handlers/witness/witness_handler.py`
- Read `WITNESS_NAMESPACE` (default `f"{name}:kel"`); pass `namespace=` into `DynamoDBer.open(...)`
  (`:95`). Review the destroy-replace comment at `:46` (the table is no longer destroyed with the
  stack — it's RETAIN in another stack); the reload-or-reincept cold-start path stays correct.

### `keri_cdk/handlers/mailbox/mailbox_handler.py`
- Read `MAILBOX_NAMESPACE` (default `f"{name}:mbx"`); pass `namespace=` into `DynamoDBer.open(...)` (`:186`).

### `keri_cdk/watcher_stack.py`
- Update the seam signature to accept `core_table: ddb.ITable`; document it uses
  `<stack-name>:kel` + the same LeadingKeys grant. Stays `NotImplementedError`.

### `ecosystems/keri_host/app.py`
- Add `core = KeriCoreStack(app, "KeriHostCore", env=env)`; pass `core_table=core.table` to both
  stacks; `add_dependency(core)`. Remove the `witness_name`/`mailbox_name` *table-name* context.
  Stack ids `KeriHostWitness`/`KeriHostMailbox` are the stack names (keeper + namespace prefix).

### NO change
- **`keri_cdk/service_aid.py` + `handlers/serviceaid/config.py`** — the Service-AID already pools
  per-service (`<alias>:kel`/`<alias>:tel`, LeadingKeys `<alias>:*#*`). It is already conformant; do
  not touch it. (Its `tel`/Reger namespace already gives credential-body confidentiality.)
- **`core_stack.py`** (already hardened) and **`src/keri/db/dynamodbing.py`** (the existing
  `namespace=` param is all B needs).

## Data flow & cutover (no migration)

Nothing CDK-side is live (federation still on SAM; Phase-B temp witness torn down; no `keri-core`
table in `personal`). Phase C is a **library change**. Runtime flow: handler cold start →
`DynamoDBer.open(table_name=<core-table>, namespace="<id>:kel|mbx")` → rows land under that prefix on
the shared table; GSI iteration / point-reads / appends byte-identical to today. Land Phase C before
the real SAM→CDK cutover; the cutover then brings everything up already pooled (preserved keeper
secrets reproduce the same AIDs). No "unpooled then migrate" state ever exists.

## Edge cases & error handling

- **`DescribeTable` vacuous-allow.** `DynamoDBer.open → _ensure_table` calls `describe_table`
  unconditionally (no item keys → vacuously allowed under LeadingKeys), but it MUST be in the action
  list or every cold start `AccessDenied`s.
- **Shared `__meta__` GSI partition.** keripy writes meta with literal `gsi_pk="__meta__"` but
  point-`GetItem`s it by PK `__meta__#{namespace}#{store}`; `__meta__#<id>:*` scopes it (probe
  confirmed cross-tenant meta GSI `Query` denied).
- **Mailbox concurrency.** Mailbox has no reserved-concurrency → concurrent writers within its own
  namespace; Phase A's `appendOnVal`/`addIoSetVal` retry (`b2255ff1`) hardens that. Distinct services
  are distinct namespaces → no contention.
- **Destroy-replace / orphaned namespaces.** Core table is protected + in its own stack behind the
  cross-stack lock; destroying a service stack never touches it. Orphaned namespaced rows of a
  decommissioned service are cleaned manually if desired (DynamoDBer clear-namespace).
- **Namespace safety.** Stack names/alias exclude `:`/`#`, so `<id>:kel` matches `<id>:*#*` cleanly.

## Testing & validation

### CDK assertions (`tests/cdk/`, `aws_cdk.assertions.Template`)
- WitnessStack/MailboxStack create **zero** `AWS::DynamoDB::Table`.
- Each carries the per-service LeadingKeys condition with its `<stack-name>` patterns.
- Env carries `*_NAMESPACE` (`<id>:kel`/`<id>:mbx`) and `*_BASER_TABLE` via `Fn::ImportValue`.
- Witness reserved-conc=1; mailbox none + `ResponseTransferMode.STREAM`; the KeriCore↔consumer
  Export/`Fn::ImportValue` lock present; the `keri_host` app has one core table, no per-service tables.
- **Update the now-obsolete tests** that assert per-service tables: `test_witness_stack.py::test_witness_owns_baser_table_with_gsi`,
  `test_keri_host_app.py::{test_keri_host_is_witness_plus_mailbox_no_core,test_witness_baser_table_name,test_mailbox_baser_table_name}`,
  and the `MailboxStack` equivalents — they assert behavior this phase removes.

### Handler / DB unit tests
- Witness/mailbox pass `namespace=` to `DynamoDBer.open` (assert via the env-driven default).
- **Isolation test** (extend `tests/db/test_dynamodbing_namespace.py`, mirroring
  `test_two_namespaces_in_one_table_are_isolated`): a witness namespace and a mailbox namespace and a
  `<alias>:tel` Reger namespace on one moto table do not collide — each reads only its own rows.

### Real-AWS temporary deploy (approved)
- `build_layer.sh` (Docker) → deploy `KeriCore` + pooled witness + mailbox to temp domains on
  `personal` → verify incept/OOBI/receipts + SSE keepalive from the shared table; rows under each
  service's namespace.
- **Re-run the LeadingKeys probe** (`keri_cdk/probes/leadingkeys/`): confirm the per-service boundary
  (`<id>:*#*`) still holds — a witness role cannot read the mailbox's or a Service-AID's namespace.
  Tear down.

## Deferred — the shared-KEL "key-state oracle" (the next focused effort, "A")

The architecturally exciting idea — a trust-domain-wide shared KEL pool so any counterparty resolved
by any service is instantly available to all — is deferred, NOT abandoned. What it requires (and why
it's its own effort):

- **A new `DynamoDBer` capability: per-store namespace routing.** `BASER_STORES` (`lambding.py:34`)
  mixes shareable public KEL stores with per-node state that must NOT be shared. Sharing the whole
  Baser is unsound (it would share the node's own habitat registry `habs./names./hbys.` and its
  processing escrows). Only a *subset* of stores is shareable:
  - **Shareable (prefix-keyed, public):** `kels. evts. fels. dtss. sigs. wigs. rcts. vrcs. aess. fons. wits. stts. ksns. knas.`
  - **Per-node (must stay private):** `habs. names. hbys.` (registry); the escrows (`pses. ooes. pwes. …`); KRAM/challenge stores.
  - keripy's own `Baser.clean()` (`basing.py:1534-1537`) already encodes this split (regenerate KEL, copy state, **omit escrows**).
- The mechanism: a `DynamoDBer` that routes a configured shareable-store set → a shared namespace
  (`shared`) and everything else → the per-service namespace, injected via the existing
  `Habery(db=...)` seam — keripy core unchanged. **No prior art** (keria isolates; selective
  per-store sharing is novel), but the data model supports it (KEL stores are prefix-keyed and
  coexist safely).
- **Build it with the micro-app-runtime**, when real Service-AIDs make the re-resolution savings
  concrete and testable, and before the production cutover (so no migration). See memory
  `project_kel_public_shared_oracle`.

## Out of scope / deferred
- The shared-KEL oracle / per-store split (above).
- The Service-AID `alias`-vs-stack-name decision (micro-app-runtime).
- The credential-presentation gate (`required_schema`/Tevery extraction still stubbed).
- The real SAM→CDK cutover trigger; the watcher handler build.

## Merge strategy
Direct merge to `development` (matches Phase A/B). Worktree `~/code/keripy/.worktrees/cdk-phaseC`,
branch `feat/cdk-phase-c`. Subagent-driven execution.
