# CDK Phase C: Pool the Infrastructure Tier onto the Shared Core Table — Design

**Date:** 2026-06-14
**Status:** Approved (brainstorm complete; ready for implementation plan)
**Repo:** keripy fork, branch `feat/cdk-phase-c` (off `development`)
**Predecessors:** Phase A (DynamoDB concurrent-append hardening, `b2255ff1`); Phase B (SAM→CDK `keri_cdk` library, `a9d11b54`)

## Goal

Make every KERI-state-bearing service in a single trust domain — witness, mailbox,
and the future watcher — share **one** `KeriCoreStack` DynamoDB table, each in its own
namespace, instead of each service owning its own `{name}-db` Baser table. Eliminate the
schema-identical duplicate tables inside a trust domain while preserving per-service
isolation via the proven `dynamodb:LeadingKeys` boundary.

## Why (the driver)

Architectural cleanliness within a trust domain. The per-service tables have the **exact
same schema and purpose** (PK/SK + `subdb-index` GSI on `gsi_pk`/`gsi_sk`); having two-plus
of them inside one operator's account/region/ecosystem is confusing and serves no isolation
purpose — they're all the same operator. The multi-tenant boundary that matters is *between
trust domains*, and that is already enforced: the distributable `keri_cdk` library is
deployed once per ecosystem, so separate operators get separate `KeriCoreStack` tables.

This is **not** primarily a scale/cost play (the infra tier is a handful of tables; the
~2,500 regional-table quota pressure comes from Service-AIDs, which already pool). It is a
"one core table per trust domain" topology decision.

## The boundary rule

**One `KeriCoreStack` table per trust domain.** A trust domain = one operator's `keri_cdk`
deployment (one AWS account/region/ecosystem). Within it, witness + mailbox + watcher +
every Service-AID pool onto the one core table, each namespaced. Across trust domains,
separate deployments → separate core tables (already how the library composes).

Pooling here also *improves* durability: the core table has RETAIN +
deletion-protection + termination-protection + PITR (`core_stack.py:24-31`); the per-service
witness/mailbox tables deliberately have **none** (`witness_stack.py:67-69`). Pooling moves
the infra tier onto the better-protected table.

## Identity & namespace convention

The **stack name** is the isolation unit for whole-stack infra services (it is already the
keeper-secret key: `keri/{Aws.STACK_NAME}/keeper`, `witness_stack.py:90`, `mailbox_stack.py:122`).

| Service | namespace | keeper | LeadingKeys patterns |
|---|---|---|---|
| Witness | `<stack-name>:kel` | `keri/<stack-name>/keeper` | `<stack-name>:*#*`, `__meta__#<stack-name>:*` |
| Mailbox | `<stack-name>:mbx` | `keri/<stack-name>/keeper` | `<stack-name>:*#*`, `__meta__#<stack-name>:*` |
| Watcher (future) | `<stack-name>:kel` | `keri/<stack-name>/keeper` | `<stack-name>:*#*`, `__meta__#<stack-name>:*` |
| Service-AID | `<alias>:kel` / `<alias>:tel` | `keri/<alias>/keeper` | `<alias>:*#*`, `__meta__#<alias>:*` |

Keys are `{namespace}#{subdb}#{hex(key)}` (`dynamodbing.py:354`); the GSI partition is
`{namespace}#{subdb}` (`dynamodbing.py:358`). This is byte-for-byte the layout the
LeadingKeys probe verified on real AWS (`keri_cdk/probes/leadingkeys/`).

CFN stack names are `[A-Za-z0-9-]` (no `:` / `#`), so `<stack-name>:kel` carries exactly the
one colon we add and matches `<stack-name>:*#*` cleanly — no collision.

**Stack name is the single stable identity anchor** for both the keeper path and the table
namespace: renaming a stack rotates both (fresh AID + empty namespace). This is the *same*
coupling the keeper already has — so the rule is simply "name the stack once, don't rename
it." AID identity is determined by the keeper salt/bran, **not** the namespace; repointing
the table never changes an AID.

### Service-AID `alias` (deferred — out of scope for Phase C)

The `ServiceAid` construct keeps its separate `alias` param rather than using a stack name,
because it is a `Construct` (witness/mailbox are `Stack`s) intended to be composed
*many-per-stack*. Whether that many-per-stack capability is kept (alias stays) or collapsed
(one-per-stack, alias = stack name) is a **micro-app-runtime** decision, not a Phase C one.
Phase C leaves `ServiceAid` untouched. See the memory note
`project_service_aid_alias_vs_stackname` for the full reasoning and the resolution options.

## Component changes (file by file)

### `keri_cdk/witness_stack.py`
- **Remove** the self-owned `ddb.Table` block (`:70-82`) and the `subdb-index` GSI it adds.
- **Add** a required `core_table: ddb.ITable` param. The table is now external; referencing
  `core_table.table_arn`/`.table_name` across the stack boundary emits the CFN
  Export/`Fn::ImportValue` lifecycle lock (same as `service_aid.py`).
- **Replace** `self.baser.grant_read_write_data(self.fn)` (`:121`, full-table) with the
  LeadingKeys-scoped `PolicyStatement` mirroring `service_aid.py:202-225`: actions
  `DescribeTable, GetItem, PutItem, DeleteItem, Query, BatchWriteItem` over
  `core_table.table_arn` + `f"{core_table.table_arn}/index/*"`, conditioned on
  `ForAllValues:StringLike` → `dynamodb:LeadingKeys: ["{Aws.STACK_NAME}:*#*", "__meta__#{Aws.STACK_NAME}:*"]`.
- **Env:** `WITNESS_BASER_TABLE = core_table.table_name`; add `WITNESS_NAMESPACE = f"{Aws.STACK_NAME}:kel"`.
- Keep `reserved_concurrent_executions=1`. Drop the now-misleading `WitnessBaserTableName`
  CfnOutput (it no longer names a witness-owned table); add a `WitnessNamespace` output instead.

### `keri_cdk/mailbox_stack.py`
- Same shape: `core_table` param, remove own `ddb.Table` (`:100-112`), swap
  `grant_read_write_data` (`:164`) for the LeadingKeys policy, env
  `MAILBOX_BASER_TABLE = core_table.table_name` + `MAILBOX_NAMESPACE = f"{Aws.STACK_NAME}:mbx"`.
- Keep **no** reserved-concurrency, both layers, and `ResponseTransferMode.STREAM` unchanged.
- Drop the now-misleading `MailboxBaserTableName` CfnOutput; add a `MailboxNamespace` output.

### `keri_cdk/handlers/witness/witness_handler.py`
- Read `WITNESS_NAMESPACE`; pass `namespace=` into `DynamoDBer.open(...)` (`:95`). Fall back to
  `name` when unset (legacy/standalone).
- Review the destroy-replace assumption at `:46` ("CloudFormation destroys the Baser table") —
  no longer true once pooled (table is RETAIN, in another stack). The reload-or-reincept
  cold-start path stays correct; update the comment/logic accordingly.

### `keri_cdk/handlers/mailbox/mailbox_handler.py`
- Read `MAILBOX_NAMESPACE`; pass `namespace=` into `DynamoDBer.open(...)` (`:186`). Fall back to
  `name` when unset.

### `keri_cdk/watcher_stack.py`
- Update the seam signature to accept `core_table: ddb.ITable`; document that the future
  watcher pools from birth at `<stack-name>:kel` with the same LeadingKeys policy. Stays
  `NotImplementedError` (no handler in Phase C).

### `ecosystems/keri_host/app.py`
- Instantiate `core = KeriCoreStack(app, "KeriHostCore", env=env)`; pass `core_table=core.table`
  to `WitnessStack` and `MailboxStack`; add `add_dependency(core)` where needed.
- Remove the `witness_name`/`mailbox_name` context (they were *table-name* overrides; the table
  is now shared). The stack ids `KeriHostWitness` / `KeriHostMailbox` are the stack names =
  isolation units.

### No change
- `keri_cdk/core_stack.py` (already hardened), `src/keri/db/dynamodbing.py` (already supports
  `namespace=`), `keri_cdk/service_aid.py` (deferred — see above).

## Data flow & cutover (no migration)

Nothing CDK-side is live (federation still on SAM; Phase-B temp witness torn down; no
`keri-core` table in `personal`). So Phase C is a **library change**, not a data operation.

Runtime flow is logically unchanged, physically repointed: handler cold start →
`DynamoDBer.open(table_name=<core-table>, namespace="<stack-name>:kel|mbx")` → all rows land
under that prefix on the shared table; GSI iteration, point-reads, and appends are identical
to today, only the PK prefix and physical table differ.

**Land Phase C in the library *before* the real SAM→CDK cutover.** Then the cutover (a
separate, deliberate, user-triggered step — unchanged from Phase B's plan) brings the infra
tier up **already pooled**: deploy `KeriCoreStack` → deploy witness/mailbox pointed at
`core.table` → preserved keeper secrets reproduce the same AIDs onto the shared table → tear
down SAM. No "unpooled then migrate" intermediate state ever exists. `KeriCoreStack` gets its
first real deployment at whichever comes first — the cutover or the first Service-AID.

## Edge cases & error handling

- **Mailbox concurrency on a shared table.** Mailbox has no reserved-concurrency → concurrent
  writers, but only within its own namespace. Phase A's local-increment-on-collision retry in
  `appendOnVal`/`addIoSetVal` (`b2255ff1`) hardens same-namespace concurrent appends.
  Cross-namespace there is no contention (disjoint PKs, disjoint `gsi_pk` partitions). A
  multi-writer mailbox coexisting with single-writer witness/Service-AID tenants is safe by
  construction.
- **Shared `__meta__` GSI partition.** keripy writes meta with PK `__meta__#{namespace}#{name}`
  but a literal `gsi_pk="__meta__"` (`dynamodbing.py:489`); it reads meta via base-table
  point-`GetItem` on the PK (`:497`), so `LeadingKeys ["__meta__#<stack-name>:*"]` scopes it
  correctly. The probe also confirmed cross-tenant meta GSI `Query` is DENIED. The literal
  shared `gsi_pk` is not a leak.
- **`DescribeTable` vacuous-allow.** `DynamoDBer.open → _ensure_table` calls `describe_table`
  unconditionally; it has no item keys so it's vacuously allowed under the LeadingKeys
  condition — but it MUST be in the action list or every cold start `AccessDenied`s.
- **Destroy-replace / orphaned namespaces.** The core table is protected and in its own stack
  behind the cross-stack lock, so destroying a witness/mailbox stack never touches it. That
  service's namespaced rows stay orphaned (no auto-cleanup); a redeploy with preserved keeper
  re-reads them (same AID). Clean wipe is manual (DynamoDBer clear-namespace path). Acceptable.
- **Namespace safety.** CFN stack names exclude `:`/`#`, so `<stack-name>:kel` matches
  `<stack-name>:*#*` with no ambiguity.

## Testing & validation

### CDK assertion tests (`tests/cdk/`, `aws_cdk.assertions.Template`)
- `WitnessStack` and `MailboxStack` each create **zero** `AWS::DynamoDB::Table` resources (the
  headline behavioral change — the table is external).
- The Lambda IAM policy carries the LeadingKeys condition with `<stack-name>` patterns.
- Env carries `*_NAMESPACE` and `*_BASER_TABLE`, the latter resolved via `Fn::ImportValue`.
- Witness keeps `reserved_concurrent_executions=1`; mailbox keeps none + `ResponseTransferMode.STREAM`.
- The `KeriCore` ↔ consumer Export/`Fn::ImportValue` lock is present.
- The `keri_host` app synthesizes one core table + witness + mailbox both pointed at it, and
  **no** per-service tables anywhere in the app.

### Handler unit tests (`tests/handlers/`, moto)
- `namespace=` is passed to `DynamoDBer.open` for witness and mailbox.
- A pooled-coexistence test seeds witness + mailbox + a service-aid namespace on one moto table
  and asserts each iterates only its own rows (moto does not enforce IAM, but this proves the
  *namespacing/iteration* is correctly scoped at the DynamoDBer level).
- Witness incept → sign → OOBI works from the namespaced shared table.

### Real-AWS temporary deploy (approved)
- `keri_cdk/layers/build_layer.sh` (Docker) → deploy `KeriCore` + pooled witness + mailbox to
  temporary domains on `AWS_PROFILE=personal`.
- Verify: one `keri-core` table; witness incepts + serves OOBI + signs receipts from the shared
  table; mailbox SSE keepalive streams; rows present under both namespaces.
- **Re-run the LeadingKeys probe** (`keri_cdk/probes/leadingkeys/`) — witness role DENIED the
  mailbox/service-aid namespaces — as cheap insurance now that infra and Service-AID tenants
  share one table.
- Tear down the witness/mailbox stacks.

## Out of scope / deferred
- The Service-AID `alias`-vs-stack-name decision (micro-app-runtime; memory
  `project_service_aid_alias_vs_stackname`).
- The real SAM→CDK cutover trigger (separate deliberate step; live 5×5 federation untouched).
- The watcher handler build (Phase C only updates the seam signature).

## Merge strategy
Direct merge to `development` when done (matches Phase A/B). Worktree
`~/code/keripy/.worktrees/cdk-phaseC`, branch `feat/cdk-phase-c`. Subagent-driven execution.
