# CDK Phase C: Shared Public KEL Pool + Scoped Credential Tier on One Core Table — Design

**Date:** 2026-06-14 (revised 2026-06-15 — re-scoped around the shared-KEL-oracle insight)
**Status:** Approved (brainstorm complete; ready for implementation plan)
**Repo:** keripy fork, branch `feat/cdk-phase-c` (off `development`)
**Predecessors:** Phase A (DynamoDB concurrent-append hardening, `b2255ff1`); Phase B (SAM→CDK `keri_cdk` library, `a9d11b54`)

## Goal

Make every KERI service in a single trust domain — witness, mailbox, and every
Service-AID — share **one** `KeriCoreStack` DynamoDB table, with the **public KEL** in a
single shared namespace that acts as a trust-domain-wide **key-state oracle**, and the only
confidential data — **ACDC credential bodies** — kept in a per-service scoped namespace. Stop
fragmenting public, verifiable key state across schema-identical per-service tables; isolate
exactly (and only) what is actually confidential.

## Why (the driver)

Two reinforcing reasons:

1. **Architectural cleanliness within a trust domain.** The per-service tables have the exact
   same schema and purpose; duplicating them inside one operator's account/region/ecosystem is
   confusing and isolates nothing — they're all the same operator. The boundary that matters is
   *between* trust domains, and that's already enforced: the distributable `keri_cdk` library is
   deployed once per ecosystem, so separate operators get separate `KeriCoreStack` tables.

2. **The KEL is public, and fragmenting it is wasteful.** A single KERI node already keeps one
   database holding every AID's KEL it has observed, keyed by AID prefix. Per-service
   namespacing fragments that — each service only benefits from counterparties *it* resolved.
   Un-fragmenting the public KEL turns the shared table into a **key-state oracle**: any
   counterparty validated by *any* service (witness, mailbox, Service-AID) is instantly
   available to all — no re-OOBI, no re-fetch from witnesses, no re-validation from inception —
   and it strengthens duplicity detection (one shared "first-seen" view).

## Research foundation (verified)

Confirmed against the KERI/ACDC specs (`kswg-acdc-specification`, `kswg-keri-specification`),
the keri.host RAG, and the keripy / keria / signify codebases:

- **KELs are public by design — no confidentiality requirement.** They hold only public keys,
  config, and digests/seals. KERI is end-verifiable with ambient verifiability.
- **KERI assumes untrusted storage.** "A corrupted store cannot inject forged events…
  corruption can only cause denial of service, not acceptance of invalid state." So a shared
  KEL store cannot cause a service to accept a forged event — and even shared *write* access is
  only a DoS risk (detectable + recoverable), not a forgery risk.
- **Caching counterparty KELs is the standard KERI operational model** — "First Seen Policy"
  and duplicity detection depend on it.
- **The only confidential data is the ACDC attribute body** (the `a`/`A` section — the values
  filled against a schema). It lives in exactly one durable store: keripy's **`Reger.creds.`**
  (`vdr/eventing.py:2400,2560`), keyed by credential SAID; transient copies in `cmse.`/`ccrd.`.
  The **Baser/KEL holds zero credential bodies**; the registry **TEL holds only digests/state**.
- **A verifier has no obligation to persist the body** — *verify-and-discard* is the documented
  production pattern (GLEIF/sally). The current keri_cdk Service-AID already discards
  (`handlers/serviceaid/handler.py:157`, `credentials=[]`, extraction not wired).
- **Witness and mailbox never hold credential bodies** (no Reger). In keria, bodies are
  agent-held, plaintext, per-controller-AID isolated; the Signify client is stateless about
  bodies. The real secret everywhere is *key material*, which lives in the keeper (Secrets
  Manager), never in this table.

**Conclusion:** the public/private line is crisp — KEL = public (share it, no read boundary);
ACDC credential bodies in the `Reger` = the one place a per-service IAM boundary protects real
confidentiality.

## The boundary rule

**One `KeriCoreStack` table per trust domain.** Within it:

- **Shared public KEL pool** — one shared namespace (`shared`) for the Baser of *every* service.
  Holds every AID's KEL (own + observed), keyed by AID prefix. No per-service read boundary.
- **Per-service scoped credential tier** — each service that issues/holds credentials keeps its
  `Reger` (credential bodies + TEL) in a per-service namespace (`<id>:reg`), `LeadingKeys`-locked.
  Witness/mailbox have no Reger, so no private tier.

Across trust domains, separate `keri_cdk` deployments → separate core tables.

Pooling also *improves* durability: the core table has RETAIN + deletion/termination protection
+ PITR (`core_stack.py:24-31`); the per-service witness/mailbox tables deliberately have none.

## Identity & namespace convention

| Service | Baser (KEL) namespace | Reger (credential) namespace | keeper |
|---|---|---|---|
| Witness | `shared` | — (no Reger) | `keri/<stack-name>/keeper` |
| Mailbox | `shared` | — (no Reger) | `keri/<stack-name>/keeper` |
| Watcher (future) | `shared` | — | `keri/<stack-name>/keeper` |
| Service-AID | `shared` (was `<alias>:kel`) | `<alias>:reg` (was `<alias>:tel`) | `keri/<alias>/keeper` |

Keys are `{namespace}#{subdb}#{hex(key)}` (`dynamodbing.py:354`); GSI partition `{namespace}#{subdb}`
(`:358`). The shared pool uses namespace `shared`; the credential tier uses the service identity
(stack name for whole-stack services, `alias` for the Service-AID). `<id>:reg` carries exactly
the one colon we add (CFN stack names and the alias are `[A-Za-z0-9-]`, no `:`/`#`).

**IAM (`dynamodb:LeadingKeys`, `ForAllValues:StringLike`):**

- **All services** get the shared-KEL grant: `["shared#*", "__meta__#shared#*"]`.
- **Services with a Reger** (Service-AID) additionally get: `["<id>:reg#*", "__meta__#<id>:reg#*"]`.

This is principled: a witness/mailbox role can touch *only* the shared KEL pool and can read
**no** credential bodies; a Service-AID can touch the shared pool **and its own** Reger, but
**not another service's** Reger (durable bodies stay confidential). A single DynamoDBer operation
never spans both namespaces (Baser and Reger are separate opens), so the `ForAllValues` union is
correct — the same shape `service_aid.py:202-225` already uses.

The keeper stays per-service in Secrets Manager — the real secret, untouched. **AID identity is
determined by the keeper salt/bran, not the namespace**, so repointing tables never changes an
AID. This also finishes the alias story: the Service-AID's `alias` now scopes *only* its private
Reger + keeper — never the shared KEL. (Whether `alias` collapses into the stack name remains the
micro-app-runtime decision — memory `project_service_aid_alias_vs_stackname`.)

### Accepted trade-off (documented)

Sharing the *whole* Baser means a service's transient `Baser.exns` (IPEX envelopes, which can
carry an inline ACDC body in transit) and the mailbox's in-transit `Mailboxer` messages ride in
the `shared` pool, readable by other roles in the **same trust domain** (same operator). This is
accepted: it is transient, same-operator, defense-in-depth-only data, and the **durable**
confidential store (`Reger.creds`) is properly scoped. A finer **per-store namespace split**
(share public KEL + TEL state; scope `exns`/messages/bodies) is a clean future refinement — it
needs a small `DynamoDBer` enhancement (per-store namespace routing) and is **out of scope** here.

## Component changes (file by file)

### `keri_cdk/witness_stack.py`
- **Remove** the self-owned `ddb.Table` (`:70-82`). Add required `core_table: ddb.ITable`
  (cross-stack ref emits the CFN Export/`Fn::ImportValue` lifecycle lock).
- **Replace** `self.baser.grant_read_write_data(self.fn)` (`:121`) with the LeadingKeys-scoped
  policy over `core_table.table_arn` + `/index/*`, conditioned on the **shared-KEL grant**
  `["shared#*", "__meta__#shared#*"]` (actions `DescribeTable/GetItem/PutItem/DeleteItem/Query/BatchWriteItem`).
- **Env:** `WITNESS_BASER_TABLE = core_table.table_name`; add `WITNESS_NAMESPACE = "shared"`.
- Keep `reserved_concurrent_executions=1`. Drop the misleading `WitnessBaserTableName` CfnOutput;
  add `WitnessNamespace`.

### `keri_cdk/mailbox_stack.py`
- Same shape: `core_table` param, remove own table (`:100-112`), swap `grant_read_write_data`
  (`:164`) for the shared-KEL LeadingKeys policy, env `MAILBOX_BASER_TABLE = core_table.table_name`
  + `MAILBOX_NAMESPACE = "shared"`. Keep no reserved-concurrency, both layers, `ResponseTransferMode.STREAM`.
  Drop `MailboxBaserTableName` output; add `MailboxNamespace`.

### `keri_cdk/service_aid.py`
- **Broaden the IAM** to the two-grant union above (`["shared#*", "__meta__#shared#*",
  "<alias>:reg#*", "__meta__#<alias>:reg#*"]`) — replacing the current `["{alias}:*#*",
  "__meta__#{alias}:*"]` (`:218`). This is the only change here; the namespaces themselves are
  derived in `config.py` (next section), and `SERVICEAID_ALIAS`/`SERVICEAID_CORE_TABLE` env are
  already passed, so no new env var is needed.
- Keeper IAM, reserved-concurrency=1, inception CR, cross-stack lock: unchanged.

### `keri_cdk/handlers/serviceaid/config.py`
- `kel_namespace` → returns the shared constant `"shared"` (was `f"{alias}:kel"`).
- `tel_namespace` → returns `f"{alias}:reg"` (was `f"{alias}:tel"`).
- (These are read by `runtime.py:130-136`, which already opens `db` and `reger` separately.)

### `keri_cdk/handlers/witness/witness_handler.py`
- Read `WITNESS_NAMESPACE` (default `"shared"`); pass `namespace=` into `DynamoDBer.open(...)`
  (`:95`). Review the destroy-replace comment at `:46` (the table is no longer destroyed with the
  stack — it's RETAIN in another stack); the reload-or-reincept cold-start path stays correct.

### `keri_cdk/handlers/mailbox/mailbox_handler.py`
- Read `MAILBOX_NAMESPACE` (default `"shared"`); pass `namespace=` into `DynamoDBer.open(...)` (`:186`).

### `keri_cdk/watcher_stack.py`
- Update the seam signature to accept `core_table: ddb.ITable`; document it pools its Baser into
  `shared` from birth with the shared-KEL grant. Stays `NotImplementedError`.

### `ecosystems/keri_host/app.py`
- Add `core = KeriCoreStack(app, "KeriHostCore", env=env)`; pass `core_table=core.table` to both
  stacks; `add_dependency(core)`. Remove the `witness_name`/`mailbox_name` *table-name* context.
  Stack ids `KeriHostWitness`/`KeriHostMailbox` are the stack names (used for keeper paths).

### `examples/gated_retrieval/app.py` + handler
- No structural change (it already composes `KeriCoreStack` + `ServiceAid`); it inherits the new
  shared-KEL / scoped-Reger behavior. Add a note that the gated example, as a *verifier*, holds no
  credential bodies; the `gated-record` it *issues* is the only thing in its scoped Reger.

### No change
- `core_stack.py` (already hardened), `src/keri/db/dynamodbing.py` (the existing `namespace=` param
  is sufficient; the per-store split is the deferred refinement).

## Data flow & cutover (no migration)

Nothing CDK-side is live (federation still on SAM; Phase-B temp witness torn down; no `keri-core`
table in `personal`). Phase C is a **library change**, not a data operation. Runtime flow:
handler cold start → `DynamoDBer.open(table_name=<core-table>, namespace="shared")` for the Baser
(and `namespace="<alias>:reg"` for the Service-AID Reger) → KEL rows land in the shared pool, the
Service-AID's credential bodies land in its scoped Reger. GSI iteration / point-reads / appends
are byte-identical to today; only the PK prefix and physical table differ.

**Land Phase C before the real SAM→CDK cutover.** Then the cutover (separate, deliberate,
user-triggered) brings everything up already pooled: deploy `KeriCoreStack` → deploy
witness/mailbox/Service-AIDs pointed at `core.table` → preserved keeper secrets reproduce the same
AIDs → tear down SAM. No "unpooled then migrate" state ever exists.

## Edge cases & error handling

- **Cross-service concurrent writes to the shared KEL.** Multiple services write the shared
  `shared#*` keyspace (own KEL + observed counterparties). Same-AID concurrent appends are
  hardened by Phase A's `appendOnVal`/`addIoSetVal` retry (`b2255ff1`). Distinct AIDs hit distinct
  PKs. Per untrusted-storage, a buggy/compromised writer can at worst DoS a row (recoverable from
  witnesses), never forge an accepted event.
- **Shared meta / DB version.** All services open the `shared` namespace, sharing meta rows
  (`__meta__#shared#<store>`, keyed by namespace+store, not instance name). Concurrent idempotent
  version writes; all services run the same keripy version.
- **`DescribeTable` vacuous-allow.** `DynamoDBer.open → _ensure_table` calls `describe_table`
  unconditionally; no item keys, so vacuously allowed under LeadingKeys — but it MUST be in the
  action list or every cold start `AccessDenied`s.
- **Shared `__meta__` GSI partition.** keripy writes meta with literal `gsi_pk="__meta__"` but
  point-`GetItem`s it by PK; the `__meta__#<ns>` LeadingKeys patterns scope it correctly (probe
  confirmed cross-tenant meta GSI `Query` denied).
- **In-domain transient exposure.** Per the accepted trade-off: transient `exns` + mailbox
  messages ride in the shared pool. Durable confidential bodies (`Reger.creds`) are scoped.
- **Destroy-replace / orphaned data.** Core table is protected + in its own stack behind the
  cross-stack lock; destroying a service stack never touches it. Orphaned rows (shared KEL is a
  growing cache — a feature; scoped Reger of a decommissioned service) are cleaned manually if
  desired (DynamoDBer clear-namespace).

## Testing & validation

### CDK assertions (`tests/cdk/`, `aws_cdk.assertions.Template`)
- WitnessStack/MailboxStack create **zero** `AWS::DynamoDB::Table`.
- Witness/mailbox IAM carries the **shared-KEL** LeadingKeys grant only (`shared#*`, `__meta__#shared#*`);
  Service-AID IAM carries the **two-grant union** (shared + `<alias>:reg#*`).
- Env carries `*_NAMESPACE = "shared"` (witness/mailbox) and `*_BASER_TABLE` via `Fn::ImportValue`.
- Witness reserved-conc=1; mailbox none + `ResponseTransferMode.STREAM`; the KeriCore↔consumer
  Export/`Fn::ImportValue` lock present; the `keri_host` app has one core table, no per-service tables.

### Handler unit tests (`tests/handlers/`, moto)
- Witness/mailbox pass `namespace="shared"` to `DynamoDBer.open`; Service-AID passes `"shared"`
  (Baser) and `"<alias>:reg"` (Reger).
- **Oracle test:** a witness writes an AID's KEL into the shared namespace; a *separate* Service-AID
  instance opened on the same moto table reads that AID's key state from the shared pool without
  re-resolution. This is the headline behavioral proof.
- **Confidentiality test:** a Service-AID's issued credential lands only under `<alias>:reg`, never
  under `shared` (moto won't enforce IAM, but proves the namespacing routes bodies correctly).

### Real-AWS temporary deploy (approved)
- `build_layer.sh` (Docker) → deploy `KeriCore` + pooled witness + mailbox to temp domains on
  `personal` → verify incept/OOBI/receipts + SSE keepalive from the shared table → confirm KEL rows
  under `shared`.
- **Re-run the LeadingKeys probe** (`keri_cdk/probes/leadingkeys/`) against the **new** boundary:
  prove a witness role is DENIED a Service-AID's `<alias>:reg` Reger (credential bodies), and ALLOWED
  the shared KEL — the boundary that now matters. Tear down.

## Out of scope / deferred
- **Per-store namespace routing** in `DynamoDBer` (share public TEL state too; scope `exns`/mailbox
  messages independently) — the finer refinement; needs a core-library change.
- The Service-AID `alias`-vs-stack-name decision (micro-app-runtime; memory note).
- The credential-presentation gate (`required_schema` / Tevery extraction still stubbed — the
  verifier currently discards; relevant because the private tier only matters for issuer/holder roles).
- The real SAM→CDK cutover trigger (live 5×5 federation untouched); the watcher handler build.

## Merge strategy
Direct merge to `development` (matches Phase A/B). Worktree `~/code/keripy/.worktrees/cdk-phaseC`,
branch `feat/cdk-phase-c`. Subagent-driven execution.
