# KERI Service AID Framework

Wrap any Python function as an autonomous KERI **Service AID**: it verifies a
signed `exn` caller (self-contained CESR), authorizes, runs your compute, and
replies with a signed **ACDC** delivered as an IPEX grant. Serverless on AWS
Lambda + DynamoDB. Generalizes `sam-witness`.

## Developer experience

```python
from serviceaid import service, Request, Reply

# issues = service.register_schema(your_schema_sad)  # computes the real schema SAID
@service.command(route="/rate/apply", issues="ESchemaRatingResult...")
def rate(req: Request) -> Reply:
    score = run_my_model(req.payload["risk_profile"])
    return Reply.acdc(recipient=req.sender,
                      attributes={"score": score, "dt": req.now()})
```

Deploy with a Python CDK app (see `examples/rating_engine/app.py`):
one shared `KeriCoreStack` per account + one `ServiceAid` per service.

## Architecture

- **Tier 1 (public KERI state):** pooled into the shared core DynamoDB table,
  namespaced per service (`{alias}:kel`, `{alias}:tel`).
- **Tier 2 (private keys):** an isolated, encrypted keeper table per service;
  the keeper passcode (`bran`) lives in Secrets Manager and engages keripy's
  at-rest encryption.
- **Tier 3 (your domain data):** your own store, owned by your stack.

## Testing

```bash
.venv/bin/python -m pytest service-aid/tests/ -v          # unit + moto integration
# Full pipeline against DynamoDB Local:
docker run -p 8000:8000 amazon/dynamodb-local
SERVICEAID_ENDPOINT_URL=http://localhost:8000 \
  .venv/bin/python -m pytest service-aid/tests/test_integration_local.py -v
```

## v1 scope & limits

Single transferable AID (witnessed at the KEL layer; **witnessed TEL issuance
completion is deferred** — see Operational must-knows); self-contained-CESR
caller verification; **allowlist authz** (required-credential authz mechanism
is present but its caller-credential extraction is deferred — see Operational
must-knows); synchronous
IPEX-grant ACDC reply; idempotency. **Out (v2+):** watcher/cached key-state,
async/long-running
compute, cross-runtime 1-of-N multisig, KMS-as-signer, non-Python compute.
High-rate KEL/TEL append serialization is v2 (see spec §14).

## Operational must-knows

- **Multi-tenant boundary is UNVERIFIED against real AWS:** per-service
  DynamoDB isolation relies on an IAM `dynamodb:LeadingKeys` condition scoping
  GSI queries by namespace. This is not validated by the test suite (moto does
  not enforce IAM conditions) and **must be confirmed before production** by
  deploying two services and attempting a cross-tenant GSI query (it must be
  denied). See `serviceaid/cdk/service_aid_construct.py`.
- **Required-credential authz is deferred:** `Policy.required_schema` exists but
  the handler does not yet extract caller-presented ACDCs (`credentials=[]`), so
  setting it denies all requests. Use the allowlist for v1 sender gating.
- **Keeper lives in one secret.** The runtime loads a single KMS-encrypted
  Secrets Manager secret (`keri/<alias>/keeper`, override with
  `SERVICEAID_KEEPER_SECRET`) holding `{salt, bran, keeper-blob}`; the bran in
  that secret drives at-rest encryption (aeid). The inception Custom Resource
  provisions it with a fresh salt+bran, so production has no plaintext-keeper
  fallback by design (a secret with an empty bran logs a warning).
- **`cdk destroy` orphans the pooled table.** KeriCoreStack's table and each
  service's keeper table use RemovalPolicy.RETAIN (intentional — losing them
  loses identities). After a destroy, the orphaned table blocks redeploy with
  ResourceAlreadyExists; re-import it before redeploying. The bran secret
  similarly enters a soft-delete recovery window.
- **SnapStart safety:** the bran is fetched inside `runtime.init()` (handler
  invocation), never at module import — so a snapshot never captures it. Do NOT
  move `init()` to module level; SnapStart would snapshot stale/absent key
  material.
- **Witnessed issuance:** v1 completes credential issuance synchronously for an
  AID whose anchor needs no witness receipts; a fully witnessed AID's issuance
  completion is deferred (see `serviceaid/issuing.py`).
- **Lost bran/keeper = unrecoverable AID.** Escrow the bran offline.
