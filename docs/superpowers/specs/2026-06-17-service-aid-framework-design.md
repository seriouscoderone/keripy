# Service-AID Framework & CDK Template — Design

**Status:** Approved (brainstorm), 2026-06-17
**Branch / worktree:** `feat/service-aid-runtime` @ `~/code/keripy/.worktrees/service-aid-runtime` (off `development` @ `15f6df2e0`)
**Supersedes the deferred items in:** `project_cdk_phase_b_shipped` (the Service-AID compute-seam, first real deploy, multi-command dispatch). Builds on Phase C + the shared KEL oracle (`2026-06-15-cdk-kel-oracle-design.md`).

---

## Goal

Turn the embryonic Service-AID code (`keri_cdk/handlers/serviceaid/`) into a **first-class, open, dependency-injected developer framework** plus a **CDK template** that deploys it — built on the mental model that a KERI **Service-AID is a declarative entity-definition surface** (the "new API"): a developer *declares an entity* (a KERI principal with a responsibility) and *fills a small set of extension points*, while the framework supplies all KERI plumbing. v1 = the foundation slice + a first real deploy.

## The mental model (north star)

- A Service-AID is the **autonomous/serverless implementation of a role** — one AID = one role = many capabilities.
- REST's "R" is a *Resource* (a noun, fixed CRUD). The Service-AID's "R" is a *Role* (an entity's capabilities, open verb space). Capabilities are addressed by **KERI exn routes**, not URL paths.
- The developer surface is small and **declarative**: declare *who* (identity), map *what* (routes → functions), and **inject** the cross-cutting concerns (authz, verification, issuance, …) as swappable providers. The extension-point surface **is the product's API** — first-class and open (extend without modifying core). Full extension taxonomy is **emergent**; v1 ships the seams we know.
- The micro-app specs in `locksmith-micro-app-designer` are **reference material for the mental model only** — NOT a contract to implement. No template loader, no UEL, no aggregates in this effort.

## Communication model (KERI-native — non-negotiable)

Grounded in `~/code/KERI-COMMUNICATION-MODEL.md`. KERI is **asynchronous message-passing over self-framing CESR streams; HTTP is only an envelope.** Never model the protocol as request/response.

- A Service-AID sits on **two boundaries**:
  - **Boundary B (server to requesters)** — agent↔agent peer messaging. Inbound is a **CESR ingest endpoint**: `POST / → 204 No Content`. The `204` carries **zero KERI meaning** — it only acknowledges ingestion.
  - **Boundary A (client to its own witnesses)** — during inception/rotation it collects its *own* receipts. As a Lambda it **must use `Receiptor` → `/receipts`** (synchronous) or mailbox-poll; it must **never** use `WitnessReceiptor` (the direct-mode push assumption silently hangs over HTTP/Lambda — keripy#1422, locksmith#77).
- A **"reply" is a new, independently-signed message routed to the requester's mailbox** — never the HTTP response. Success → an IPEX **grant** delivered via `forwarding.Poster` to the requester's reachable endpoint. In the *native model*, denial/rejection is a **signed spurn / denial exn** to the mailbox; **v1 ships grant-on-success + silence on every other outcome** (deny / reject / error / unknown route / bad sig → log, no reply). Signed denials are a named follow-on (see Scope).
- The Lambda does the work **synchronously within the invocation** (it has no after-response execution), but the **KERI result leaves out-of-band to the mailbox**; the HTTP layer returns `204`. The requester polls its mailbox (SSE `qry route="mbx"`) for the reply.
- CESR-over-HTTP: inbound is reassembled via `parseCesrHttpRequest` (body `application/cesr` + `CESR-ATTACHMENT` header) into the parser buffer, identical to TCP. The parser is transport-blind.

**The only HTTP error that is "real"** is a malformed CESR envelope (transport `400`). Every KERI-semantic outcome is a signed message to the mailbox or deliberate silence.

---

## Core abstractions (`keri_serviceaid` — the framework package, shipped in a layer)

```python
@dataclass
class Request:
    sender: str            # verified caller AID prefix
    route: str             # the signed exn `r`
    payload: dict          # exn `a` block
    credentials: list      # verified presented ACDCs (populated by Verifier; [] under Allowlist authz)
    message_said: str      # exn SAID — the idempotency key
    key_state: "KeyState"  # resolved sender key state + assurance tier

@dataclass
class Reply:
    kind: str              # "acdc" | "none" | "reject"
    recipient: str | None; attributes: dict | None
    edges: dict | None; rules: dict | None; reason: str | None
    @classmethod
    def acdc(cls, *, recipient, attributes, edges=None, rules=None): ...
    @classmethod
    def none(cls): ...
    @classmethod
    def reject(cls, *, reason): ...

class ServiceAid:
    """The declared entity. Constructed in the developer's compute_code module."""
    def __init__(self, *, alias, witnesses=None, toad=0,
                 authz: Authorizer = None, verifier: Verifier = None,
                 resolver: Resolver = None, issuer: Issuer = None,
                 deliverer: Deliverer = None, idempotency: IdempotencyStore = None):
        # any provider left None → its default (below)
    def command(self, *, route: str, issues: str = ""):  # decorator → registers a Command
        ...
```

- **`ServiceAid`** holds identity config + the injected providers + the command registry. The dev names it (`svc = ServiceAid(...)`); the framework finds it via the `module:attr` entry ref.
- **`Command`** = `(route, payload_schema, issues, fn)`. Decorator-registered. The routing spine. `route` must not start with `/ipex/` (reserved — framework rejects it). One command per route (duplicate → error).
- Payload-schema validation rides on `Command.payload_schema` (optional JSON-Schema), not a separate provider (YAGNI; promotable later).

## Extension points — six injected Protocols, each with a default

```python
class Authorizer(Protocol):
    def authorize(self, req: Request) -> tuple[bool, str]: ...     # (allow, reason)
# default: Allowlist(aids)            follow-on: CredentialGate(required_schema=…)

class Verifier(Protocol):
    def verify(self, sender: str, ims: bytes, hby) -> KeyState: ... # raise VerificationError if tier unmet
# default: OracleVerifier(tier="receipts")      tiers: "signed" | "receipts" | "watcher"(future)

class Resolver(Protocol):
    def resolve(self, sender: str, hby) -> Endpoint: ...           # where to deliver the reply
# default: OracleResolver(fallback=[InStream(), Oobi()])           # ends/locs/eans now in the oracle

class Issuer(Protocol):
    def issue(self, reply: Reply, ctx: Context) -> bytes: ...       # signed IPEX grant exn
# default: IpexGrantIssuer()

class Deliverer(Protocol):
    def deliver(self, msg: bytes, endpoint: Endpoint, ctx: Context) -> None: ...  # async, Postman /fwd
# default: PostmanDeliverer()

class IdempotencyStore(Protocol):
    def seen(self, said: str) -> bytes | None: ...   # prior grant on replay
    def record(self, said: str, grant: bytes) -> None: ...
# default: DynamoLedger(db)
```

**Design rule:** "open extension point" = "new Protocol + default impl", no edits to the pipeline core. Defaults make a minimal service ~5 lines.

**Developer experience:**
```python
from keri_serviceaid import ServiceAid, Reply, Allowlist, Request

svc = ServiceAid(alias="mvr-bureau", witnesses=[...], toad=2,
                 authz=Allowlist(["EReq1…", "EReq2…"]))   # inject only what you override

@svc.command(route="/mvr/cmd/request_record", issues="ESchema…")
def request_record(req: Request) -> Reply:
    return Reply.acdc(recipient=req.sender, attributes=lookup(req.payload["vin"]))
```

**Entry ref (the layer↔asset seam):** `SERVICEAID_HANDLER="mvr_handler:svc"` (ASGI-style `module:attr`). The framework handler (in the layer) imports the dev module, grabs `svc`, and drives the pipeline against its providers.

---

## The request pipeline (transport-silent, mailbox-out)

**Cold start (cached warm singleton):** open `db` (Baser) on the shared oracle namespace (`shared_namespace="shared", shared_stores=SHARED_KEL_STORES`) + own private namespace; open `reger` (Reger) **private**; keeper from Secrets Manager; build Habery; **incept-or-load the witnessed AID, collecting receipts via `Receiptor`/`/receipts`** (never `WitnessReceiptor`); ensure registry; publish own end-role + OOBI; `import module:attr` → `svc` + providers; register schemas; add one capture-behavior per route to `hby.exc`.

**Per inbound (Boundary B; all paths end in `204`, result leaves via mailbox):**

1. Reassemble CESR-over-HTTP → parser buffer (`parseCesrHttpRequest`). (CR `RequestType` events fork to inception `on_event`.)
2. `hby.psr.parse` → sender KEL lands in the oracle; Kevery verifies the exn against oracle key state; Exchanger captures the verified exn. **`‹Verifier›`** asserts the assurance tier; bad sig / tier unmet → **drop, no reply**.
3. Dispatch by the **signed `r`** → `Command`. No command → no behavior → **no reply**.
4. **`‹IdempotencyStore.seen(said)›`** hit → **re-deliver** the recorded grant to the mailbox (do **not** re-compute/re-issue).
5. **`‹Authorizer.authorize›`** deny → **v1: log, no reply** (native model: signed spurn/denial exn → mailbox — follow-on).
6. `cmd.fn(req)` → `Reply`. Raises → log, **no reply**, **not** recorded (safe re-send).
7. Branch:
   - `acdc` → **`‹Issuer.issue›`** → grant; **`‹IdempotencyStore.record(said, grant)›`** (BEFORE delivery); **`‹Resolver.resolve›`** → endpoint; **`‹Deliverer.deliver›`** (Postman `/fwd`) → mailbox.
   - `reject` → **v1: log, no reply** (native: signed spurn/denial — follow-on).
   - `none` → nothing delivered.
8. Return **`204 No Content`** (no KERI meaning).

**Exactly-once issuance / idempotent re-delivery:** `record` happens after `issue` but before `deliver`. Issuance (which mints a TEL credential) runs **once**; a delivery failure + client re-send hits `seen()` and **re-delivers the same grant — never re-issues** → no duplicate credentials, at-least-once delivery, zero extra infra. (Client-driven retry in v1; a DLQ/EventBridge auto-retry so the client needn't re-send is a follow-on.)

**Denials, the native model (follow-on):** a denial would be a *signed* message — IPEX inbound → `/ipex/spurn`; custom-command inbound → a denial-note exn (e.g. `/<eco>/note/denied`) — never a status code. **v1 ships grant + silence** (deny/reject/error/unknown → log, no reply); signed denials are a named follow-on so the first cut stays minimal and sidesteps resolving reachability for unauthorized senders.

---

## Discovery & verification (the two separated concerns)

KEL/TEL are public logs. Identity (AID) ≠ key state (KEL) ≠ reachability (end-role/loc). A bare AID is self-certifying but **not self-locating**.

**Verification (key state) — three sources, default oracle:**
- **(c) Oracle** (default): the shared `shared#` namespace already carries key events **and witness receipts** (`wigs./rcts./vrcs.`) → **witness-corroborated (tier-2)** key state from a local read for any in-domain or served-before AID. No explicit lookup — parsing the inbound KEL into the shared namespace makes the kever available in `hby.kevers`.
- **(a) In-stream**: a first-contact requester's `KEL [+receipts]` rides in the request; on parse it lands in the oracle (corroborated as far as its declared witnesses' receipts).
- **(b) OOBI pull**: resolve the requester's OOBI outbound for KEL + end-roles.
- **Assurance tiers:** `signed` (tier-1) | `receipts` (tier-2, default) | `watcher` (tier-3, future — the `keri_cdk` watcher seam). First-contact-without-receipts under `receipts` → drop (strict default).

**Reachability (reply routing):** the oracle is made **reachability-complete** by sharing `ends./locs./eans.` (see Oracle change below). So **(c)** resolves the requester's mailbox endpoint from one local read for in-domain peers; **(a)** in-stream end-roles (land in the private ns, persist for reuse) and **(b)** OOBI are fallbacks for first-contact.

**Registration / "waiting to serve them"** = the authz facet of (c): the `Allowlist` names *who*; the oracle + cache supply their *key state + reachability*.

## Inception & witnessing

The Service-AID's own AID is incepted **witnessed** (`wits`/`toad` from config). As a Lambda it collects receipts via **`Receiptor`/`/receipts`** or mailbox-poll — **never `WitnessReceiptor`**. A regression guard test asserts this. (This also unblocks the witnessed-TEL issuance path that `issuing.py` currently warns is incomplete on the witnessed branch; completing witnessed issuance is in scope insofar as the first real deploy is witnessed — if it proves heavy, the deploy may fall back to a minimally-witnessed/`toad`-appropriate config, decided at plan time.)

---

## CDK packaging seam

**Two layers + the dev's asset:**

| Artifact | Contents | Owner |
|---|---|---|
| `KeriRuntimeLayer` (exists) | libsodium + keripy + uvicorn (arm64/py3.14) | framework |
| `ServiceAidFrameworkLayer` (new) | the `keri_serviceaid` package — handler entry, pipeline, contract, default providers | framework |
| `compute_code` (the seam) | the dev's command module (`svc = ServiceAid(...)` + `@svc.command`) + custom providers + schemas | developer |

**Construct (`keri_cdk.ServiceAidFunction`, renamed from `ServiceAid`):**

```python
class ServiceAidFunction(Construct, iam.IGrantable):
    def __init__(self, scope, cid, *, alias, core_table: ddb.ITable,
                 compute_code: _lambda.Code,
                 handler_ref: str = "service:svc",   # module:attr
                 witnesses=None, toad=0,
                 runtime_layer=None, framework_layer=None,
                 environment=None, memory=1024, timeout_seconds=120,
                 vpc=None, extra_layers=None, **kw):
        self.function = _lambda.Function(self, "Function",
            code=compute_code,
            handler="keri_serviceaid.handler.handler",        # entry resolves from the framework layer
            layers=[klayer, flayer, *(extra_layers or [])],
            environment={**framework_env, **(environment or {})},
            reserved_concurrent_executions=1, memory_size=memory,
            timeout=Duration.seconds(timeout_seconds), vpc=vpc)
    @property
    def grant_principal(self):                # iam.IGrantable
        return self.function.grant_principal
```

- **Inherited unchanged (Phase B/C):** cross-stack core-table lock (`core_table: ITable` → Export/`Fn::ImportValue`); the four-pattern `LeadingKeys` union (`shared#*`, `__meta__#shared#*`, `{alias}:*#*`, `__meta__#{alias}:*`); keeper-secret IAM scoped to `keri/<alias>/*`; inception Custom Resource (the Function doubles as `on_event`).
- **API Gateway:** CESR ingest — proxy, `binary_media_types=["application/cesr","*/*"]`, REGIONAL, returns `204`.
- **`IGrantable` payoff:** the adopter grants their own resources the canonical way — `my_lookup_table.grant_read_data(svc)`; `.function` is the escape hatch.
- **Real-deploy unknown (must validate):** the layer-resident handler `keri_serviceaid.handler.handler` (`/opt/python`) importing the dev's `/var/task` module which imports the framework layer, with libsodium from `KeriRuntimeLayer` (`/opt/lib`). Fallback if Lambda won't resolve a layer-resident handler: a 3-line `handler.py` shim auto-injected into the asset (`from keri_serviceaid.handler import handler`).

**Layer build:** `ServiceAidFrameworkLayer` build script `pip install`s the `keri_serviceaid` package into the layer (mirrors `KeriRuntimeLayer`'s `build_layer.sh`); gitignored asset; built in CI before any `cdk deploy`.

## Oracle change (small, contained)

Add `ends.`, `locs.`, `eans.` to `SHARED_KEL_STORES` (`keri/app/lambding.py`) so path-(c) is reachability-complete. These are not in `NEVER_SHARE_STORES`; the four-pattern IAM already grants `shared#*`. Extend the `LeadingKeys` oracle probe + tests to cover the three added stores.

## The gated example (reworked)

Migrate `examples/gated_retrieval/` to the new framework: `compute_code` module declaring a `ServiceAid` with **≥2 routes** (e.g. `/gated/cmd/request_record` + `/gated/cmd/revoke_record`), `Allowlist` authz, fictional `gated-record` ACDC. This is the artifact for the first real deploy.

---

## Testing strategy

- **Provider unit tests** (no AWS/keripy where possible): each Protocol impl against fakes — `Allowlist`, `OracleVerifier` tier logic, `OracleResolver` role-priority fallback, `IpexGrantIssuer` grant shape, `DynamoLedger` (moto), `PostmanDeliverer` (fake Poster).
- **Command tests** via an in-memory `TestRuntime` (drive `cmd.fn(req)`, assert `Reply`; no keripy).
- **Pipeline tests** with fake providers injected into `ServiceAid`: assert *behavior, not HTTP* — grant delivered to endpoint X; deny → spurn; replay → re-deliver, **not** re-issue; compute-raised → nothing recorded.
- **Integration (moto + fake mailbox):** cold-start on moto DynamoDB with the oracle namespace; incept; ingest a real signed `KEL+exn`; verify via oracle; compute → issue → deliver into a fake mailbox; assert grant landed. Plus a **cross-Habery oracle read** test (two Haberys sharing `shared#` on one moto table → AID-A's kever visible to service-B).
- **CDK synth tests** (`assertions.Template`): two layers; `handler="keri_serviceaid.handler.handler"`; `compute_code` wired; env merge; four-pattern `LeadingKeys`; keeper IAM; inception CR; API-GW CESR ingest; an **`IGrantable`** test (`grant_read_data` adds a policy to the Function role).
- **Regression guard:** assert the inception path uses `Receiptor`/`/receipts`, never `WitnessReceiptor`.
- **First real deploy = the integration test synth cannot be** (then torn down): validates (1) layer-resident handler resolution + libsodium; (2) witnessed inception via `Receiptor`; (3) oracle verification of a real inbound exn; (4) Postman delivery to a real mailbox (`mailbox.keri.host`); (5) the IPEX round-trip (requester polls SSE, admits) across **≥2 routes** on one AID.

Tests run via the worktree's own `.venv` (`.venv/bin/python -m pytest`), moto for AWS, default import mode.

---

## Scope

**In scope (v1 foundation slice):**
1. The `keri_serviceaid` framework package (entity, Request/Reply, Command registry, the six provider Protocols + default impls, the pipeline, cold-start runtime, idempotency).
2. Rework of the inbound handler from the synchronous-overlay to **transport-silent `204` + mailbox-out** (incl. signed-denial path or grant+silence per plan-time decision).
3. Witnessed inception via **`Receiptor`** (not `WitnessReceiptor`).
4. `ServiceAidFrameworkLayer` + the `ServiceAidFunction` construct (`compute_code`, `IGrantable`, curated pass-through, layer-resident handler + shim fallback).
5. `SHARED_KEL_STORES += ends./locs./eans.` (reachability-complete oracle) + probe/test extension.
6. Gated example reworked (≥2 routes) and **deployed for real**, then torn down.
7. The AWS-free test pyramid + the first-real-deploy validation.

**Out of scope (named follow-ons):**
- **`CredentialGate` authz** — presented-ACDC verification via Tevery extraction + `required_schema` (the level-(b) "prove-then-retrieve" gate). The crown-jewel follow-on.
- **Watcher tier-3** verification (the `keri_cdk` watcher seam).
- **Signed denials** — spurn (IPEX) / denial-note exn delivered to the mailbox on deny/reject (v1 is grant + silence).
- **DLQ/EventBridge auto-retry** of delivery (v1 is client-retry + idempotent re-deliver).
- **Mailbox-inbound option** (Service-AID drains its own `mailbox.keri.host` on a schedule) — v1 uses a direct CESR-ingest endpoint.
- Micro-app template loader, UEL, aggregates, projections, reactions, workflows (explicitly not this effort).

## Naming

- `keri_serviceaid.ServiceAid` — the runtime entity the developer writes (star of the show).
- `keri_cdk.ServiceAidFunction` — the CDK construct that deploys one (renamed from `ServiceAid` for zero ambiguity).

## File structure (drives the plan)

- **New package** `keri_serviceaid/` (top-level, pip-installable, shipped in the layer): `__init__.py`, `contract.py` (ServiceAid, Request, Reply, Command, TestRuntime), `providers/` (`authz.py`, `verify.py`, `resolve.py`, `issue.py`, `deliver.py`, `idempotency.py` — Protocols + defaults), `pipeline.py`, `handler.py` (entry + CR fork + CESR-over-HTTP reassembly), `runtime.py` (cold start, Receiptor inception), `config.py`, `bootstrap.py` (libsodium shim). Migrated/refactored from `keri_cdk/handlers/serviceaid/`.
- **Modified** `keri/app/lambding.py` (`SHARED_KEL_STORES += ends./locs./eans.`).
- **New** `keri_cdk/framework_layer.py` (`ServiceAidFrameworkLayer`) + `keri_cdk/layers/build_framework_layer.sh`.
- **Rewritten** `keri_cdk/service_aid.py` → `ServiceAidFunction` (compute_code + framework layer + IGrantable + pass-through).
- **Modified** `examples/gated_retrieval/` (new framework, ≥2 routes, real-deploy app).
- **Tests:** `tests/serviceaid/` (providers, pipeline, command), `tests/handlers/` (integration, oracle read), `tests/cdk/` (synth, IGrantable), probe extension under `keri_cdk/probes/leadingkeys/`.
- **Removed/retired:** the synchronous-overlay handler internals and the old `keri_cdk/handlers/serviceaid/` once migrated.

## Resolved architecture decisions

1. Async exn/IPEX; reply = signed message to the requester's mailbox; never the HTTP response.
2. Dispatch by the **signed `r`**, not the HTTP path; HTTP is pure transport (`204`).
3. Verification via the shared **KEL oracle** (witness-corroborated tier-2; watcher = future tier-3); in-stream / OOBI fallbacks.
4. Reachability via the oracle (`ends/locs/eans` shared) + in-stream/OOBI fallbacks.
5. Service-AID inbound = direct **CESR-ingest endpoint** (Boundary B); its own witnessing uses **`Receiptor`** (Boundary A); never `WitnessReceiptor`.
6. Extension points are **injected Protocols** with defaults; commands via decorator; entry via `module:attr`.
7. CDK seam = `compute_code` + `ServiceAidFrameworkLayer` + construct-owns-Function + `IGrantable` + curated pass-through; layer-resident handler with shim fallback.
8. Exactly-once issuance + idempotent re-delivery (`record` between `issue` and `deliver`).
9. v1 authz = `Allowlist`; `CredentialGate` is the named follow-on.
