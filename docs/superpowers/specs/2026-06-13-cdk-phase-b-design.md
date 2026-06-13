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
│   ├── service_aid.py           #   ServiceAid construct + inception CR (moved)
│   ├── watcher_stack.py         #   WatcherStack           (SEAM ONLY: props + skeleton)
│   └── handlers/                #   generic infra Lambda code (pure Python)
│       ├── witness/             #     moved from sam-witness/
│       ├── mailbox/             #     moved from sam-mailbox/
│       └── serviceaid/          #     generic Service-AID runtime framework
│                                #     (config/contract/issuing/authorize/runtime/handler),
│                                #     moved from service-aid/serviceaid/
├── ecosystems/
│   └── keri_host/               # the keri.host federation app — WITNESS + MAILBOX only
│       ├── app.py               #   cdk.App composing WitnessStack + MailboxStack
│       └── cdk.json
├── examples/
│   └── gated_retrieval/         # library-usage example (validates ServiceAid + core-table lock)
│       ├── app.py               #   composes KeriCoreStack + ServiceAid(gated lookup)
│       ├── gated_handler.py     #   made-up business compute + gated-record ACDC schema
│       └── cdk.json
└── (keripy core unchanged; old service-aid/, sam-witness/, sam-mailbox/ removed)
```
The old `sam-witness/`, `sam-mailbox/`, and the `service-aid/serviceaid/` framework package are
**absorbed into `keri_cdk`** (clean slate). Generic infra logic — witness + mailbox handlers AND the
**Service-AID runtime framework** (config/contract/issuing/authorize/runtime/handler) — **moves into
the library**; every ecosystem reuses identical protocol machinery. Only Service-AID *business compute*
+ its ACDC schemas stay app-side (the `gated_retrieval` example is the pattern; the app supplies a
`handler_module`).

**keri.host needs no Service-AID** — its ecosystem app is witness + mailbox (the federation infra). The
**Gated Retrieval** Service-AID is a library-usage *example* (`examples/gated_retrieval/`) that doubles
as the e2e validation of the `ServiceAid` construct + `KeriCoreStack` + the cross-stack lock.

### Example: the Gated Retrieval Service-AID (made-up, generic)

A "prove-then-retrieve" Service-AID modeled on the insurance/credit-bureau pattern (Verisk MVR, credit
reports) — entirely fictional schemas. A *Requestor* (e.g. an insurer) wants *gated data* about a
*Subject*: it sends a signed `exn` **gated request** → the Service-AID's **authorize gate** confirms the
requestor is permitted → it runs a made-up **gated lookup** compute → it returns a **gated-response
ACDC** (a generic `gated-record` credential with "cool data") via IPEX grant.

**Gate fidelity for Phase B = level (a): allowlist-themed.** The requestor's AID is on the Service-AID's
allowlist (= "proved access"); the response is the `gated-record` ACDC. This uses the **shipped**
framework as-is (allowlist authz is wired + reachable) — no new framework feature, stays an
infra-conversion. The made-up ACDCs (`gated-access`, `gated-record`) are defined as generic example
schemas. The fuller **level (b)** — requestor *presents* a `gated-access` ACDC the service *verifies* —
is a deferred follow-on (see Out of Scope).

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
    **API-Gateway response streaming** (SSE long-poll). This runtime model MUST be preserved:
    - **LWA as a SEPARATE Lambda layer** — the AWS-published arm64 LWA layer (`from_layer_version_arn`,
      pinned to the current version for the region/arch), alongside `KeriRuntimeLayer`.
    - **LWA zip wiring** (the fiddly bit — implementer verifies against the current LWA layer): a
      `run.sh` entrypoint launching `uvicorn app:app --host 0.0.0.0 --port $PORT` via
      `AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap` (the documented zip pattern), OR the extension style the
      current container uses; env `AWS_LWA_INVOKE_MODE=response_stream`, port (`PORT`/`AWS_LWA_PORT`),
      `AWS_LWA_READINESS_CHECK_PATH=/status`.
    - **API-Gateway streaming, CDK-native (this SIMPLIFIES vs SAM):** API Gateway REST gained native
      response streaming (Nov 2025); recent `aws-cdk-lib` exposes it as
      `apigw.LambdaIntegration(response_transfer_mode=apigw.ResponseTransferMode.STREAM)` — NO escape
      hatch (the SAM template needed an explicit `AWS::ApiGateway::Method` with raw `ResponseTransferMode`
      only because SAM/OpenAPI couldn't express it). **Implementer must verify the pinned `aws-cdk-lib`
      version supports `ResponseTransferMode.STREAM`; if it predates it, fall back to a `CfnMethod`
      escape hatch** (`responseTransferMode: STREAM` + the `.../response-streaming-invocations`
      integration URI). Endpoint MUST be **REGIONAL** (edge-optimized buffers and defeats streaming);
      integration timeout up to **15 min** (STREAM lifts the old 29s ceiling).
    - **Preserve the existing SSE generator logic** (`_stream_mbx_response` + its keep-alive pings) —
      do NOT rewrite to Falcon-native `resp.sse`/`SSEvent` in Phase B; that's a behavior-changing
      handler refactor noted as a possible FUTURE cleanup, not part of this conversion.

    This is the most complex stack in Phase B — the real-AWS smoke (below) MUST cover the mailbox
    streaming path (an actual SSE long-poll message delivery), not just the witness.
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
  for a foundational append-only ledger table). This replaces the current loose by-name reference in
  the `ServiceAid` construct; the lock is exercised by the **`examples/gated_retrieval` app**
  (`KeriCoreStack` + the Gated Retrieval `ServiceAid`), since the keri_host ecosystem app has no
  Service-AID.
- **Witness/mailbox keep their OWN Baser tables** in Phase B (each `WitnessStack`/`MailboxStack`
  creates its own `<name>-db` table, as the SAM templates did). They consume `KeriCoreStack` only
  in Phase C (pooling). So the cross-stack lock in Phase B applies to the Service-AID ↔ core-table
  edge (the gated_retrieval example).

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
- **Full deploy validation (two apps)** to `personal`:
  - `cdk deploy ecosystems/keri_host` → witness + mailbox come up on their own tables; the **mailbox
    SSE long-poll delivers a message** (validates LWA + response-streaming end-to-end, not just cold
    start).
  - `cdk deploy examples/gated_retrieval` → `KeriCoreStack` + the Gated Retrieval `ServiceAid` come up;
    the **Service-AID ↔ core-table cross-stack lock** is in place; and a **gated request → allowlist
    gate → gated-response ACDC** exchange works end-to-end (the existing Service-AID issuance path,
    re-themed).
- Existing keripy core suites stay green (the handler moves must not change behavior beyond the
  responder retries; the moved handler code keeps its current logic).

## Out of scope (explicit)

- **Phase C** — pooling witness/mailbox/watcher onto `KeriCoreStack` (namespacing them onto the
  shared table) + any associated migration. Witness/mailbox keep their own tables here.
- **Building a working watcher** — seam only.
- **Gated Retrieval level (b): true credential-presentation gate** — the requestor *presents* a
  `gated-access` ACDC the Service-AID *verifies* via `Policy.required_schema`. Deferred follow-on; it
  completes keripy's stubbed required-credential authz (shipped v1 passes `credentials=[]`). Phase B's
  example uses the allowlist gate (a). See `project_gated_retrieval_credential_gate` memory.
- **x86_64** runtime — arm64 only.
- **Publishing `keri_cdk` to PyPI/CodeArtifact** — structure it as publishable, but the actual
  publish pipeline + KEL-anchored release automation is a later effort.
- **A no-Docker container fallback / SAR distribution** — the zip+layer model is the chosen path.
- **Building additional ecosystems** (gym, etc.) — they are future apps on the library.
- **Mailbox long-poll cost/concurrency tuning** — Lambda bills wall-clock for the whole time an SSE
  long-poll is held open and pins one concurrency unit per open connection. A production mailbox may
  want an optional reserved-concurrency *ceiling* (to cap blast radius) and/or a shift to an API
  Gateway WebSocket API (which holds the connection off-Lambda). Noted as a future operational/cost
  decision; Phase B keeps the mailbox uncapped (no reserved-concurrency) and on the SSE/LWA path.
- **Falcon-native `resp.sse`/`SSEvent` SSE refactor** — possible future cleanup of the mailbox's
  hand-rolled SSE framing; Phase B preserves the working generator.

## Execution

Subagent-driven (implementer + spec-compliance review + code-quality review per task), final
whole-branch review, then merge to `development`. The real-AWS smoke + ecosystem deploy are
invoked/inspected by the controller on `personal` (operator pre-authorized real-AWS testing).
