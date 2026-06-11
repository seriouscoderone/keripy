# KERI Service AID Framework

Wrap any Python function as an autonomous KERI **Service AID**: it verifies a
signed `exn` caller (self-contained CESR), authorizes, runs your compute, and
replies with a signed **ACDC** delivered as an IPEX grant. Serverless on AWS
Lambda + DynamoDB. Generalizes `sam-witness`.

## Developer experience

```python
from serviceaid import service, Request, Reply

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

Single transferable+witnessed AID; self-contained-CESR caller verification;
allowlist + required-credential authz; synchronous IPEX-grant ACDC reply;
idempotency. **Out (v2+):** watcher/cached key-state, async/long-running
compute, cross-runtime 1-of-N multisig, KMS-as-signer, non-Python compute.
High-rate KEL/TEL append serialization is v2 (see spec §14).

## Operational must-knows

- **Multi-tenant boundary is UNVERIFIED against real AWS:** per-service
  DynamoDB isolation relies on an IAM `dynamodb:LeadingKeys` condition scoping
  GSI queries by namespace. This is not validated by the test suite (moto does
  not enforce IAM conditions) and **must be confirmed before production** by
  deploying two services and attempting a cross-tenant GSI query (it must be
  denied). See `serviceaid/cdk/service_aid_construct.py`.
- **Run without a bran = plaintext keeper keys.** The runtime logs a warning;
  set `SERVICEAID_BRAN_SECRET` to a Secrets Manager secret for production.
- **Witnessed issuance:** v1 completes credential issuance synchronously for an
  AID whose anchor needs no witness receipts; a fully witnessed AID's issuance
  completion is deferred (see `serviceaid/issuing.py`).
- **Lost bran/keeper = unrecoverable AID.** Escrow the bran offline.
