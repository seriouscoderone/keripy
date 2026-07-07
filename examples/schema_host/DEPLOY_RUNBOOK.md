# Schema Host Service-AID — First Real Deploy Runbook

The schema.keri.host Service-AID integration validation that a synth test CANNOT do.
Run it ONCE against real AWS to prove (1) layer-resident handler resolution +
libsodium; (2) witnessed inception via Receiptor; (3) artifact storage & CAS
read-plane delivery; (4) serializable first-seen dedup; (5) idempotent schema
publish + receipt re-delivery. Then tear it down.

## Prereqs
- AWS creds for the target account (`AWS_PROFILE=personal`), region with python3.14 Lambda.
- Docker (for arm64 layer builds).
- A test publisher keystore (kli) whose AID is on the allowlist.
- CloudFront + Route53 + hosted zone (`hosted_zone_id`) for DNS CNAME to `schema.keri.host`.
- Inception Custom Resource shares the service Function, whose handler routes
  `RequestType` events to `keri_cdk._inception`. Confirm `_inception` resolves at
  deploy (a CR failure with an ImportError means the asset is missing it).

## 1. Build BOTH layers (arm64, in Docker)
```bash
# KeriRuntimeLayer (includes new DynamoDBer.claimFirstSeen)
keri_cdk/layers/build_layer.sh

# ServiceAidFrameworkLayer (includes artifact_store.py, Reply.publish, publish pipeline branch)
keri_cdk/layers/build_framework_layer.sh
```
Confirm each prints `OK: ...` and a sane size.

## 2. Deploy (single stack)
```bash
cd examples/schema_host
# Optional witnessed config (recommended to exercise Receiptor):
#   --context witnesses='["BWit1","BWit2"]' --context toad=2
#   (the 5×5 federation; OOBI-resolve them into the core table first)
cdk deploy KeriCore SchemaHost \
  --context account=<acct> \
  --context region=us-east-1 \
  --context domain=schema.keri.host \
  --context hosted_zone_id=<z> \
  --context allowlist='["<publisher-aid>"]'
```
The inception Custom Resource runs on Create: it get-or-creates the keeper secret
(keri/schema-publisher/keeper), incepts the AID, and initializes the registry
table. **If witnessed, confirm the CR collected receipts via Receiptor
(/receipts), NOT WitnessReceiptor** — check the Function's CloudWatch logs for
"Service AID inception complete: alias=schema-publisher pre=E..." with no
hang/timeout (a WitnessReceiptor hang would surface as a CR timeout — the
regression guard in tests/serviceaid/test_runtime_v2.py prevents reintroducing
it).

## 3. Resolve the service OOBI into the publisher
```bash
# The API Gateway URL is a stack output; the service published its own end-role.
kli oobi resolve --name pub --oobi-alias schema-publisher --oobi <apigw-url>/oobi/<pre>/controller
```

## 4. POST a signed exn — publish (write plane)
Build a signed `/schema/cmd/publish` exn from the publisher keystore (recipient =
the service pre). The exn body carries `{schema: <a test ACDC schema SAD>,
want_receipt: true}`. POST it as `application/cesr` with `CESR-ATTACHMENT` header
to the CloudFront root (it routes `/schema/*` to the API Gateway).

```bash
# Example (using kli + keripy tools):
# 1. Create a test schema (ACDC SAD)
# 2. Embed in exn: {v: "KERI10JSON...", t: "exn", d: <said>, r: "/schema/cmd/publish", ...}
# 3. Sign with publisher keystore
# 4. POST to https://schema.keri.host/schema/cmd/publish
```
Expect **HTTP 204**. The receipt leaves out-of-band to the publisher's mailbox.

## 5. Verify the read plane
```bash
# The schema is now in S3 (CAS) behind CloudFront
curl -i https://schema.keri.host/oobi/<schema-said>
# Expect HTTP 200, Content-Type: application/schema+json
# Body: a JSON object with $id == <schema-said>

# Verify in Python:
from keri.core.scheming import Schemer
import json
body = ...  # GET response
schema = json.loads(body)
Schemer(raw=body)  # Should not raise; SAID matches
assert schema['$id'] == '<schema-said>'
```

## 6. Verify the ledger
The publisher's mailbox received an `/ipex/grant` for a `publication_receipt`
ACDC (issued by the schema-publisher Service-AID). Poll and admit it:

```bash
kli mailbox poll --name pub           # or the SSE route='mbx'
# Expect an /ipex/grant for a publication_receipt ACDC
kli ipex admit --name pub --said <grant-said>

# Verify attributes in the credential (kli credential list --name pub):
# - firstSeen: true
# - dt: <server-stamped ISO8601>
# - schema_said: <schema-said>
```

## 7. First-seen dedup
From a SECOND publisher keystore (different AID, also on the allowlist), publish
the SAME schema (identical sad). Retrieve the receipt from its mailbox:

```bash
# Repeat steps 4–6 with publisher-2
# The receipt differs from step 6:
# - firstSeen: false
# - priorContributor.aid: <publisher-1-aid>
# - schema_said: <schema-said> (SAME)

# Confirm the S3 CAS object is unchanged (idempotent):
# - object key: oobi/<schema-said>
# - content: IDENTICAL to the first publish
```

## 8. Replay (idempotency)
Re-POST the EXACT exn from step 4 (same signature, same said). Expect **HTTP 204**
and the publisher's mailbox to receive the SAME receipt again (re-delivered, not
re-issued — no duplicate credential SAID in the TEL).

```bash
kli mailbox poll --name pub
# Expect the SAME publication_receipt (same $id in the credential)
# Confirm only ONE TEL issuance event (one iss, no additional entries)
```

## 9. Tear down
```bash
cdk destroy SchemaHost        # the AID/keeper secret persist by design (CR Delete is a no-op)
# KeriCore (the pooled table) outlives services; destroy only if no other service consumes it.
```

## Validation checklist (all must hold)
- [ ] Both layers built (`build_layer.sh` + `build_framework_layer.sh`) and attached; cold start imports keri.
- [ ] Inception completed via Receiptor (no WitnessReceiptor hang); logs show "Service AID inception complete".
- [ ] Write plane (publish): POST /schema/cmd/publish → HTTP 204.
- [ ] Read plane: GET /oobi/<schema-said> → HTTP 200, `application/schema+json`, `$id` matches.
- [ ] Registry receipt: publisher's mailbox received publication_receipt ACDC; admitted successfully.
- [ ] First-seen dedup: second publisher's receipt has `firstSeen: false` + `priorContributor.aid` pointing to publisher-1.
- [ ] CAS object idempotency: S3 key and content unchanged across both publishes.
- [ ] Replay re-delivered the same receipt (exactly-once issuance; one TEL `iss` event).
