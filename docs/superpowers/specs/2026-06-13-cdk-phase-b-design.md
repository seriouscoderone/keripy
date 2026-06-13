# Unified CDK App — keri_cdk Library + keri-host Ecosystem (Phase B) — Design

**Status:** approved 2026-06-13
**Branch:** `feat/cdk-phase-b` (worktree `.worktrees/cdk-phaseB`, off `development`)
**Scope:** Phase B of the SAM→CDK / pooled-core-table effort. Convert the serverless KERI
stacks from SAM to a unified, distributable Python **CDK construct library** (`keri_cdk`)
plus the **first ecosystem app** (`ecosystems/keri_host`). Phase A (dynamodbing concurrent-append
hardening) already shipped (keripy `development` `b2255ff1`). Phase C (pooling witness/mailbox
onto `KeriCoreStack`) is a later, separate effort.

## Vision context (why this shape)

Two artifacts with a clean boundary:

- **`keri_cdk`** — a distributable, importable, eventually-publishable construct library. It is
  THE product: other companies, in any AWS account, `pip install keri_cdk`, write their own CDK
  app, and compose KERI services. Therefore the library is **account- and domain-agnostic** — no
  hardcoded `keri.host`, no hardcoded 5-federation. All environment specifics are **props**.
- **One CDK app per ecosystem** — the consumer. The first is `ecosystems/keri_host` (the keri.host
  federation). Future ecosystems (e.g. a "gym" with membership/instructor/payment Service-AIDs)
  are their own apps built ON the library — explicitly NOT designed here.

This repo (the keripy fork) is the testbed: it hosts both the library and the first ecosystem app.

## Architecture

### Stack decomposition

Per-service stacks + shared infra, wired by the ecosystem app (CDK-idiomatic composition):
`KeriCoreStack` (shared, stateful) and each of `WitnessStack` / `MailboxStack` / `ServiceAid`
are independent stacks; the app instantiates them and wires the shared pieces. Rejected: a
monolithic ecosystem stack (couples all lifecycles, CFN resource limits, fights composition) and
nested stacks (shared lifecycle, harder independent deploy).

### Repo & package layout
```
keripy/
├── keri_cdk/                    # the distributable construct library (THE PRODUCT)
│   ├── __init__.py              #   exports: KeriCoreStack, KeriRuntimeLayer, WitnessStack,
│   │                            #            MailboxStack, ServiceAid, WatcherStack
│   ├── core_stack.py            #   KeriCoreStack          (moved from service-aid/serviceaid/cdk)
│   ├── runtime_layer.py         #   KeriRuntimeLayer       (libsodium + keripy native deps)
│   ├── witness_stack.py         #   WitnessStack           (new; zip + layer)
│   ├── mailbox_stack.py         #   MailboxStack           (new; zip + layer)
│   ├── service_aid.py           #   ServiceAid + inception (moved)
│   ├── watcher_stack.py         #   WatcherStack           (SEAM ONLY: props + skeleton)
│   └── handlers/                #   generic infra Lambda code (pure Python)
│       ├── witness/             #     moved from sam-witness/ (handler + helpers)
│       └── mailbox/             #     moved from sam-mailbox/ (handler + helpers)
├── ecosystems/
│   └── keri_host/               # the FIRST ecosystem app (the consumer)
│       ├── app.py               #   cdk.App composing keri.host's stacks
│       └── cdk.json
├── service-aid/examples/rating_engine/   # stays — the Service-AID business-compute example
└── (keripy core unchanged)
```
The old `sam-witness/` and `sam-mailbox/` directories are **removed** (clean slate). Generic infra
handlers (witness, mailbox) **move into the library** (`keri_cdk/handlers/`) — every ecosystem
reuses identical protocol logic. Only Service-AID *business compute* stays app-side (the
`rating_engine` example is the pattern; the app supplies a `handler_module`).

### Runtime model: zip + KeriRuntimeLayer (arm64)

The two crypto-bearing infra Lambdas (witness, mailbox) and the Service-AID run as **zip Lambdas
with pure-Python handlers**, riding a shared **`KeriRuntimeLayer`** that carries the native
dependencies (libsodium via `pysodium`, plus keripy's other arch-specific wheels —
`cryptography`, `blake3`, `cbor2`, etc.). **arm64 only** for now (matches the current Lambdas;
add x86_64 only if an adopter requires it).

- The layer artifact is built at **library-release time in CI** on an Amazon-Linux/arm64 builder
  (Docker or an AL container there) — **never at consumer deploy time**. It is shipped inside the
  `keri_cdk` package. Consequence: consumers deploy with **no Docker requirement** — `cdk deploy`
  uploads the prebuilt layer + the pure-Python function zips. The existing Dockerfiles are repurposed
  as the layer-build input and kept as a fallback.
- `KeriRuntimeLayer` is a CDK construct (`aws_lambda.LayerVersion` from the shipped asset). It is
  **stateless/immutable**, so it gets **no cross-stack lock** — each service stack instantiates it
  from the shared library asset (CDK dedupes the upload by content hash). Locking it would add
  stiffness for no benefit.
- Handler env: `pysodium`/the loader finds libsodium via `LD_LIBRARY_PATH=/opt/lib` (Lambda layers
  extract under `/opt`; this replaces the container's `/var/task/lib`).
- **Two different function shapes** — the conversion is NOT uniform:
  - **Witness** is a standard API Gateway proxy Lambda (request → `witness_handler.handler(event, context)` →
    response). The simplest zip+layer conversion: pure-Python handler + `KeriRuntimeLayer`.
  - **Mailbox** is a **Falcon ASGI app served by uvicorn behind the Lambda Web Adapter (LWA)** with
    **API-Gateway response streaming** (SSE long-poll). This runtime model MUST be preserved: the zip
    function carries the Falcon app + bootstrap; **LWA is provided as a SEPARATE Lambda layer** (the
    AWS-published arm64 LWA layer, alongside `KeriRuntimeLayer`); the LWA env is preserved
    (`AWS_LWA_INVOKE_MODE=response_stream`, `AWS_LWA_PORT`, `AWS_LWA_READINESS_CHECK_PATH=/status`);
    and the API Gateway REST integration's `ResponseTransferMode=STREAMING` (the explicit-method setup
    from the SAM template) is reproduced in CDK. This is the most complex stack in Phase B — the
    real-AWS smoke (below) must cover the mailbox streaming path, not just the witness.
- Layer + function unzipped size must stay under the **250 MB** Lambda limit (libsodium is tiny;
  keripy + deps are modest — verified during the build task).
- **Provenance:** the layer is built transparently in CI from pinned source and rides the anchored
  source release (the keri.host publisher-entity model) — not an opaque hosted binary.

### Cross-stack wiring & the lifecycle lock

- **`KeriCoreStack`** — the shared, stateful pooled table. Hardened to production-grade now (even
  though Phase B starts clean-slate): **point-in-time recovery (PITR) enabled**,
  **`deletion_protection=True`** on the table, **`termination_protection=True`** on the stack
  (the existing `# TODO(before production)` in the current code). The `ServiceAid` stack consumes
  the table via a **real CDK cross-stack reference** (`core.table` object, NOT a by-name literal),
  which generates the CloudFormation `Export`/`Fn::ImportValue` **lifecycle lock** — CloudFormation
  then refuses to delete/replace the table's export while a service imports it (the desired guard
  for a foundational append-only ledger table). This replaces the current loose by-name reference
  in `service_aid_construct.py` + `examples/rating_engine/app.py`.
- **Witness/mailbox keep their OWN Baser tables** in Phase B (each `WitnessStack`/`MailboxStack`
  creates its own `<name>-db` table, as the SAM templates did). They consume `KeriCoreStack` only
  in Phase C (pooling). So the cross-stack lock in Phase B applies to the Service-AID ↔ core-table
  edge.

### Concurrency & resilience (the Phase-A follow-ons)

- **`reserved_concurrent_executions=1`** on the **witness** and **Service-AID** Lambda functions —
  one logical writer per AID. Per-AID KERI throughput is tiny, so serializing is free and makes
  eventual-consistency lag only ever affect the same writer's later reads (escrow-absorbed). **NOT**
  set on the **mailbox** (it serves many concurrent recipients; Phase A's `appendOnVal` retry already
  made its concurrent same-topic deposits safe without serializing).
- **False-404 responder retries** (handler-layer, where DynamoDB-awareness is appropriate): a
  bounded retry on a *negative* result in the synchronous responders that gate on an eventually-
  consistent GSI read — witness `GET /receipts` (`kels.getLast` / `wigs.get`), OOBI `fullyWitnessed`,
  and the query (`ksn`/`logs`) path — so sub-second GSI lag does not surface as a spurious 404. A
  positive result is trusted immediately (no retry); only the not-found/under-threshold path retries
  with a small bounded backoff. Implemented in the moved handler code (`keri_cdk/handlers/`).

### Account/domain-agnostic props (the adoption requirement)

Every library stack/construct takes explicit props with **zero hardcoded keri.host or 5-federation
values**: `domain_name`, `hosted_zone_id`, `witnesses` (list), `toad`, `region`, keeper-secret name
(convention-defaulted), alias/name. `ecosystems/keri_host/app.py` supplies keri.host's values;
another company supplies theirs. ACM cert + API Gateway custom domain + Route53 record are created
per-service from these props (as the SAM templates did).

### Clean-slate deployment (no migration)

The existing SAM witnesses/mailbox/watchers and the throwaway core table are **non-production and
disposable** (operator-confirmed 2026-06-13). Cutover is therefore clean-slate: `sam delete` /
`cdk destroy` the old stacks, then `cdk deploy` the fresh CDK stacks on the real domains. **No temp
domains, no flip, no mailbox drain, no data migration.** **Fresh AIDs** — the keeper-secret
get-or-create auto-mints fresh salts (operator may seed saved salts later for stable federation
AIDs, but that is optional and not required here). The greenfield path removes migration pain
WITHOUT lowering the quality bar — the fresh deployment is built production-grade (lock, PITR,
deletion protection, reserved-concurrency, responder retries, the runtime layer).

### Watcher seam (design only, do NOT build)

`WatcherStack` ships as a props/skeleton class with a clean construct API and NO handler
implementation (a documented `NotImplementedError` or minimal stub). A future ecosystem adds a
working watcher by composition; building the watcher handler (KEL observation / duplicity checking)
is net-new feature work for a separate, later effort.

## Testing & validation

- **CDK synth/assertion tests** per stack (`aws_cdk.assertions.Template`): correct resources, Lambda
  env vars, IAM statements (scoped keeper-secret access, scoped DynamoDB access), `reserved_concurrent_executions`
  on witness/Service-AID, the cross-stack export/import lock on the core table, PITR + deletion
  protection on `KeriCoreStack`, the layer attached to each function. moto/synth-level — fast,
  deterministic.
- **Real-AWS zip+layer smoke** (the load-bearing validation that the runtime model works): deploy a
  zip+layer witness to `personal`/us-east-1, confirm it incepts, signs, and serves an OOBI — proving
  `pysodium`/libsodium resolve from `/opt/lib` in a real Lambda. Joins the existing
  `service-aid/probes/` family in spirit (a `keri_cdk` validation script).
- **Full ecosystem-app deploy validation**: `cdk deploy` `ecosystems/keri_host` to `personal`,
  confirm the witness + mailbox + Service-AID come up, the Service-AID ↔ core-table lock is in place,
  an end-to-end exchange works (the existing Service-AID e2e path), AND the **mailbox SSE long-poll
  delivers a message** (validates the LWA + response-streaming model end-to-end, not just cold start).
- Existing keripy core suites stay green (the handler moves must not change behavior beyond the
  responder retries; the moved handler code keeps its current logic).

## Out of scope (explicit)

- **Phase C** — pooling witness/mailbox/watcher onto `KeriCoreStack` (namespacing them onto the
  shared table) + any associated migration. Witness/mailbox keep their own tables here.
- **Building a working watcher** — seam only.
- **x86_64** runtime — arm64 only.
- **Publishing `keri_cdk` to PyPI/CodeArtifact** — structure it as publishable, but the actual
  publish pipeline + KEL-anchored release automation is a later effort.
- **A no-Docker container fallback / SAR distribution** — the zip+layer model is the chosen path.
- **Building additional ecosystems** (gym, etc.) — they are future apps on the library.

## Execution

Subagent-driven (implementer + spec-compliance review + code-quality review per task), final
whole-branch review, then merge to `development`. The real-AWS smoke + ecosystem deploy are
invoked/inspected by the controller on `personal` (operator pre-authorized real-AWS testing).
