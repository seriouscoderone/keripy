# KERI Service AID Framework — Design Spec

| | |
|---|---|
| Status | Approved for planning (2026-06-10) |
| Repo | keripy fork (`development` branch), co-located beside `sam-witness/` |
| Predecessor | `~/code/locksmith-micro-app-designer/docs/superpowers/specs/2026-05-15-micro-app-runtime-design.md` (superseded/refocused by this) |

## 1. Thesis & goal

A **Service AID** is an autonomous KERI principal: an AID-bearing entity that receives a signed `exn`, verifies and authorizes the sender, runs arbitrary compute, and replies with a signed **ACDC** attributing the result to itself. Service AIDs replace backend REST APIs — the protocol surface (KEL / ACDC / IPEX / SAID) is richer and the schemas deeper, with trust established organically via credentials rather than designed by committee.

**Goal:** a general, templated, AWS-CDK-packaged, serverless framework that lets a developer wrap *any* Python algorithm (Rating Engine, etc.) as a Service AID, deployed quickly and repeatably. Target scale: thousands of KERI services (witnesses, watchers, judges, jurors, juries, service AIDs) — all the *same construct family*, differing only in their compute and AID transferability.

This is **not KERIA.** KERIA is an agent acting *on behalf of* a controller who retains signing authority via Signify. A Service AID is an autonomous principal that holds its own will and signs its own attestations; an owner *governs* it (via delegation/kill-switch) but does not author its claims.

## 2. Settled decisions (from brainstorming)

1. **Compute coupling:** in-process Python handler — the developer's function runs in the same Lambda as the KERI verify/sign machinery, sharing the Habery + registry. Synchronous reply is the natural case.
2. **State posture:** fully serverless, DynamoDB-backed, reusing the fork's `keri/db/dynamodbing.py` (`DynamoDBer`) + `keri/app/lambding.py` (setup_* + warm Habery singleton) + the `sam-witness/` deployment pattern. **The hard part is already built.**
3. **Caller key-state:** self-contained CESR only for v1 — the caller presents their KEL inline with the signed request (exactly how `witness_handler.py` already works via `_extract_cesr_stream` → `psr.parse`). No watcher, no key-state cache.
4. **Reply:** a signed **ACDC** delivered as an IPEX grant, returned synchronously in the HTTP response body. This is the **first real exercise of `lambding.setup_reger`** (the witness never issues credentials).
5. **Identity:** the Service AID is **transferable + witnessed** (unlike a witness, which is non-transferable/fixed-key). Cross-runtime 1-of-N multisig deferred to v2+.
6. **Packaging:** Python CDK (`aws-cdk-lib`) construct + a **Custom Resource** that incepts the AID + registry on stack-create. (Diverges from Locksmith's TS `infrastructure/` — separate concern; this framework is Python end-to-end because the handler is keripy/Python.)
7. **Placement:** co-located in the keripy fork beside `sam-witness/`. The fork already diverges greatly from upstream; can be extracted to a standalone repo later.

## 3. Architecture

**One line:** generalize `sam-witness` — keep its serverless KERI core (Habery on `DynamoDBer` via `lambding`, warm singleton, API Gateway → Lambda → DynamoDB) and swap the witness's fixed receipt-compute for a developer-supplied Python function, adding an authorize gate and ACDC issuance.

**Two-layer deployment topology** (this is the key scaling decision):

- **Shared `KeriCoreStack`** — deployed *once per account/environment*. Owns the **pooled, multi-tenant** KERI-core DynamoDB table(s) holding *public* KERI state (KEL/Baser, Reger/TEL, Noter, Mailboxer) for *all* services, namespaced per service AID. Exported via SSM/CfnOutput.
- **Per-service thin stack** (one per Service AID) — just: Lambda (container image) + API Gateway + an IAM role scoped to *its* namespace prefix in the shared table + its inception Custom Resource + an **isolated, encrypted keeper** + any service-specific (Tier-3) domain DB. References the shared core table; does not create it.

The `ServiceAid` construct takes the shared table as a prop:
`new ServiceAid(this, { coreTable, keeperSecret, alias, witnesses, toad, issues, authorization, handler })`.

**Data tiers:**
- **Tier 1 — core KERI state:** uniform prefixed KV; pooled into the shared table (DynamoDB single-table design).
- **Tier 2 — private key material (keeper):** isolated per service, encrypted (see §7). Never pooled.
- **Tier 3 — service domain data:** the algorithm's own data (risk models, reporting); whatever fits (RDS / own DynamoDB / S3); owned by the service stack.

## 4. Package layout

New `service-aid/` in the keripy fork, beside `sam-witness/`:

```
service-aid/
  serviceaid/                  # framework (pip package)
    handler.py                 # generic Lambda entrypoint: init() + handler() — generalizes witness_handler.py
    contract.py                # developer-facing API: @service.command, Request, Reply
    authorize.py               # authz policy: allowlist + required-credential
    issuing.py                 # ACDC issuance: build creder, anchor TEL event in Reger, frame IPEX grant
    config.py                  # env-driven: alias, witnesses, toad, schema SAIDs, authz, handler import path
  cdk/
    service_aid_construct.py   # ServiceAid construct (consumes shared coreTable; provisions Lambda+APIGW+IAM+CustomResource+keeper)
    keri_core_stack.py         # shared KeriCoreStack: pooled core DynamoDB table(s)
    inception.py               # Custom Resource handler: incept AID + registry on create
  examples/rating_engine/      # reference Service AID: handler.py + app.py (CDK)
  Dockerfile                   # container image (mirrors sam-witness)
  tests/
```

## 5. Developer contract

The developer writes one Python function — the whole surface:

```python
from serviceaid import service, Request, Reply

@service.command(route="/rate/apply", issues="ESchema_RatingResult...")
def rate(req: Request) -> Reply:
    score = run_my_model(req.payload["risk_profile"])   # arbitrary in-process compute
    return Reply.acdc(
        recipient=req.sender,                 # verified caller AID
        attributes={"score": score, "rated_at": req.now()},
        edges={"profile": req.payload_said},  # chain result to input
    )
    # also: Reply.none(), Reply.reject(reason="...")
```

- `@service.command` registers the function and declares which ACDC schema it may issue.
- `Request`: `sender` (verified AID), `payload` (verified exn attributes), `credentials` (verified attached ACDCs via Tevery), `message_said` (idempotency key), `payload_said`, `now()`.
- `Reply.acdc(...)` is **declarative** — the framework does registry issuance, signing, IPEX-grant framing. The developer never touches keripy.

## 6. Request data flow

**Cold start (`init()`, generalizes `witness_handler.init`):**
1. Open `DynamoDBer` instances; `lambding.setup_baser/keeper/reger` wire a `Habery(temp=False, free=True, db=..., ks=..., bran=<from Secrets Manager>)`.
2. Load-or-incept the **transferable, witnessed** service AID (`makeHab(transferable=True, wits=[...], toad=N, ...)`), and its credential registry (Reger).
3. Import the developer's handler module (entry point / env import path); register `@service.command`s.
4. Warm module-level singleton across invocations.

**Per request (`handler(event, context)`):**
1. Extract CESR stream from request body.
2. Parse through the Habery's parser → verifies sender signature against the **inline KEL** (self-contained CESR); attached ACDCs validated by Tevery.
3. Pull the `exn`; route to the registered handler by `route`.
4. **Authorize:** sender allowlist and/or required-credential present+verified. Fail → IPEX `spurn` / 403.
5. **Dispatch:** call the developer function with `Request`.
6. **Reply:** `Reply.acdc` → issue credential in Reger (anchor TEL event to KEL), frame IPEX grant, return CESR in HTTP 200 body.
7. **Idempotency:** dedupe on `message_said` (DynamoDB `processed_messages/<said>`); duplicate → return cached ack, do not re-run/re-issue.

## 7. Keeper custody (security — investigated, see findings)

**Findings (keripy fork, evidenced):**
- The Keeper is a **separate** store from the Baser (its own env / its own DynamoDB table). Holds root salt, per-AID seeds/private keys (`pris`), pre-rotation ciphers, key situation. (`keeping.py:136,228,279-304`)
- keripy keeper at-rest encryption is **conditional — only when an `aeid` is set.** No aeid → **plaintext** on disk / in DynamoDB. (`keeping.py:769-778,902-925`; `subing.py:1898-1961`) The decrypting seed lives in memory only, re-supplied each run from a passcode/`bran`; never persisted.
- The deployed **witness currently runs with NO keeper encryption**: only `salt` is passed to `Habery` (no `bran`/`aeid`), and the salt is a **plaintext CloudFormation parameter → env var** (`WITNESS_SALT`), so **private keys sit unencrypted in the `-ks` DynamoDB table**. (`witness_handler.py:45,101,115`; `template.yaml:24-27,116`)
- **No KMS-as-signer path exists** in the fork; keripy signs in-process (libsodium Ed25519), so the seed must reach memory regardless. KMS-never-leaves-HSM signing is out of scope (would need a custom keripy `Signer` delegating to KMS Sign).
  - **[ADDENDUM 2026-06-10, post-approval — this bullet is outdated.]** AWS KMS supports pure Ed25519 signing since Nov 2025 (`ECC_NIST_EDWARDS25519`, `ED25519_SHA_512`/`MessageType=RAW` = RFC 8032, libsodium-verifiable), and keripy has a stubbed `Algos.extern` seam plus full P-256 support. KMS-as-signer is feasible as a ~300–500 LOC additive adapter and is slated as a v2 high-assurance tier. See `2026-06-10-keeper-custody-aws-findings.md` for the full security-panel findings (threat model, keeper-is-a-cache analysis, custody ladder, costs). v1 custody below is unchanged.

**AWS-appropriate design for a transferable Service AID:**
1. **Engage keripy's aeid keeper encryption** — pass a `bran` to `Habery` so keys at rest in DynamoDB become ciphertext.
2. **Load that `bran` from Secrets Manager at cold start** (via boto3), optionally **KMS-envelope-wrapped**. Plaintext exists only transiently in Lambda memory.
3. **Tight IAM:** the function reads exactly one secret / KMS key.
4. **Keeper stays out of the pooled table** — isolated per service (its own keeper DynamoDB table or namespace + its own secret).

## 8. State namespacing (prerequisite — investigated, see findings)

**Findings (keripy fork, evidenced):** multi-tenant namespacing is **NOT in place**. The DynamoDB key is `{subdb_name}#{hex(key)}` (`dynamodbing.py:329-339`); the service/Habery `name` goes **only into the table name** (`keri-{name}`, `dynamodbing.py:235-236`), never the key. Pooling services into one table today would **collide unsafely** (identical PK/SK → silent overwrite/cross-read). The GSI `gsi_pk = db.name` (`dynamodbing.py:429`) also lacks a tenant prefix.

**Change required (small, well-seamed, but a storage-format change):** prefix the three key formatters `_pk`, `_gsi_pk`, `_gsi_sk` (`dynamodbing.py:329-339`) with the service-AID/tenant (`self.name`, already on the instance at `dynamodbing.py:195`):
```
_pk:     f"{self.name}#{db.name}#{_hex(key)}"
_gsi_pk: f"{self.name}#{db.name}"
```
Also update the `_clear_store`/meta paths (`dynamodbing.py:464-465,488`). Add a storage-format version flag; existing single-tenant tables need migration or stay on the old format. This is the **prerequisite for the shared `KeriCoreStack`** pooling.

## 9. Authorization

Two policies, composable, evaluated after KERI verification:
- **Allowlist:** sender AID ∈ configured set.
- **Required credential:** caller must present a valid ACDC of a configured schema SAID (verified via Tevery).
KERI verification confirms *authenticity* (the message is really from that AID); authorization confirms *permission*.

[ADDENDUM 2026-06-10, post-implementation] Required-credential authz is implemented as a Policy mechanism but its caller-ACDC extraction (Tevery) is NOT wired in the v1 handler (credentials=[]); allowlist authz is fully functional. Required-credential authz is deferred to a follow-up.

## 10. Error handling & idempotency

| Failure | Behavior |
|---|---|
| CESR/sig/KEL/ACDC verification fails | reject, no handler call, structured log, HTTP 4xx (cannot sign a meaningful KERI reply to an unverified party) |
| Authorization fails | IPEX `spurn` if applicable; 403 |
| Handler raises | caught, structured log, **not** recorded as processed (retry-safe), HTTP 5xx |
| Duplicate (`message_said` seen) | short-circuit; no re-run, no re-issue; return idempotent ack |

Idempotency + effect application: write message-SAID + a Decision summary atomically so a crash mid-apply either leaves no record (retried) or a complete record (done). Net: at-least-once delivery + exactly-once application of effects.

## 11. Testing

- **Unit:** developer contract via an in-memory `TestRuntime` (fakes for Hab/registry); fast (<1s).
- **Integration (local):** DynamoDB Local + real Habery on `DynamoDBer`; send real self-contained-CESR requests through full verification; assert issued ACDC verifies. Mirror `sam-witness/test_live.py`.
- **Live (not CI):** deployed stack + the 5-witness federation, `AWS_PROFILE=personal`.

## 12. Prerequisite tasks (do before/with v1)

1. **`dynamodbing` namespacing change** (§8) — required for pooled `KeriCoreStack`. Small, format-versioned.
2. **Keeper custody** (§7) — engage aeid encryption + Secrets Manager `bran` loader at cold start; isolated keeper.
3. **First real `setup_reger` exercise** — the registry path has never issued an ACDC end-to-end in the fork; validate it.
4. **(Separate hardening task, tracked but possibly out of v1 scope)** — the existing `sam-witness` stores private keys in plaintext in DynamoDB and ships a plaintext salt CFN parameter. Worth fixing regardless via the same Secrets-Manager + aeid approach.

## 13. v1 scope

**IN:** single transferable+witnessed Service AID; DynamoDB-backed via `lambding`; self-contained-CESR caller verification; allowlist + required-credential authz; in-process Python handler contract (`@service.command` + `Reply`); synchronous IPEX-grant ACDC reply; idempotency; CDK `ServiceAid` construct + shared `KeriCoreStack` + inception Custom Resource; isolated aeid-encrypted keeper w/ Secrets Manager bran; one reference example (rating engine).

[ADDENDUM 2026-06-10, post-implementation] Required-credential authz is implemented as a Policy mechanism but its caller-ACDC extraction (Tevery) is NOT wired in the v1 handler (credentials=[]); allowlist authz is fully functional. Required-credential authz is deferred to a follow-up.

**OUT (v2+):** watcher / cached caller key-state; async / long-running compute & mailbox-delivered replies; workflow / aggregate / scheduling sugar; cross-runtime 1-of-N multisig identity; KMS-as-signer (key never in memory); non-Python compute targets.

## 14. Open questions (carry into planning, non-blocking)

- KEL/TEL append concurrency under high issuance rate: DynamoDB conditional writes (optimistic) vs a FIFO queue serializing appends per AID. Decide in plan.
- Handler registration mechanism: decorator + entry-point vs env import path.
- Whether the Reger shares the pooled core table or its own (Reger is public KERI state → poolable; confirm `setup_reger` store layout).
- Service-AID key rotation operations (transferable AID) and the governance/delegation kill-switch wiring (delegator AID) — likely v1.1.
