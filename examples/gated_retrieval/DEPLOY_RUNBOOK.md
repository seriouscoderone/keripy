# Gated Retrieval Service-AID — First Real Deploy Runbook

The Service-AID framework's integration validation that a synth test CANNOT do.
Run it ONCE against real AWS to prove (1) layer-resident handler resolution +
libsodium; (2) witnessed inception via Receiptor; (3) oracle verification of a
real inbound exn; (4) Postman delivery to a real mailbox (mailbox.keri.host);
(5) the IPEX round-trip across ≥2 routes on one AID. Then tear it down.

## Prereqs
- AWS creds for the target account (`AWS_PROFILE=...`), region with python3.14 Lambda.
- Docker (for the arm64 layer builds).
- A test requester keystore (kli) that can sign exns and poll a mailbox via SSE.
- Deploy-layout contract: the inception Custom Resource shares the service
  Function, whose handler routes `RequestType` events to `keri_cdk._inception`.
  That module must be importable from the deployed asset — either `keri_cdk` is on
  the path or `_inception.py` rides flat in the asset (`/var/task`). The example's
  `app.py` uses the example dir as `compute_code`; confirm `_inception` resolves at
  deploy (a CR failure with an ImportError here means the asset is missing it).

## 1. Build BOTH layers (arm64, in Docker)
```bash
keri_cdk/layers/build_layer.sh              # KeriRuntimeLayer: libsodium + keripy
keri_cdk/layers/build_framework_layer.sh    # ServiceAidFrameworkLayer: keri_serviceaid
```
Confirm each prints `OK: ...` and a sane size.

## 2. Deploy
```bash
cd examples/gated_retrieval
# Optional witnessed config (recommended to exercise Receiptor):
#   --context witnesses='["BWit1","BWit2"]' --context toad=2
#   (the 5×5 federation; OOBI-resolve them into the core table first)
cdk deploy KeriCore GatedRetrieval \
  --context account=<acct> --context region=<region>
```
The inception Custom Resource runs on Create: it get-or-creates the keeper secret
(keri/gated/keeper) and incepts the AID. **If witnessed, confirm the CR collected
receipts via Receiptor (/receipts), NOT WitnessReceiptor** — check the Function's
CloudWatch logs for "Service AID inception complete: alias=gated pre=E..." with no
hang/timeout (a WitnessReceiptor hang would surface as a CR timeout — the
regression guard in tests/serviceaid/test_runtime_v2.py prevents reintroducing it).

## 3. Resolve the service OOBI into the requester
```bash
# The API Gateway URL is a stack output; the service published its own end-role.
kli oobi resolve --name reqr --oobi-alias gated --oobi <apigw-url>/oobi/<pre>/controller
```

## 4. POST a signed exn — route 1 (request_record)
Build a signed /gated/cmd/request_record exn from the requester (recipient = the
service pre), POST it as application/cesr + CESR-ATTACHMENT to the API GW root.
Expect **HTTP 204**. The grant leaves out-of-band to the requester's mailbox.

## 5. Requester polls its mailbox (SSE) + admits
```bash
kli mailbox poll --name reqr           # or the SSE qry route='mbx'
# Expect an /ipex/grant for a gated-record ACDC; admit it:
kli ipex admit --name reqr --said <grant-said>
```
Confirm the gated-record ACDC is now in the requester's credential store.

## 6. Exercise route 2 (revoke_record)
POST a signed /gated/cmd/revoke_record exn. Expect **HTTP 204** and (v1 grant+
silence) NO mailbox reply. Confirm the Function logged the revoke with no error.
This proves ≥2 capabilities on ONE role/AID.

## 7. Replay (idempotency)
Re-POST the EXACT request_record exn from step 4. Expect **HTTP 204** and the
requester's mailbox to receive the SAME grant again (re-delivered, not re-issued
— no duplicate credential SAID). Confirm only ONE issuance occurred (one TEL iss).

## 8. Tear down
```bash
cdk destroy GatedRetrieval        # the AID/keeper secret persist by design (CR Delete is a no-op)
# KeriCore (the pooled table) outlives services; destroy only if no other service consumes it.
```

## Validation checklist (all must hold)
- [ ] Both layers built and attached; cold start imports keri (libsodium resolved).
- [ ] Inception completed via Receiptor (no WitnessReceiptor hang).
- [ ] A real inbound exn verified against the oracle key state.
- [ ] Grant delivered via Postman to the requester's mailbox (mailbox.keri.host).
- [ ] IPEX round-trip: requester polled SSE + admitted the credential.
- [ ] ≥2 routes exercised on one AID (request_record + revoke_record).
- [ ] Replay re-delivered the same grant (exactly-once issuance).
