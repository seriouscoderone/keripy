# Phase 1 — OOBI Spec Compliance (Design)

**Roadmap phase:** 1 of 4 (see `2026-04-21-lambda-witness-roadmap.md`)
**Size:** Small
**Status:** Design approved; ready for implementation planning

## Context

The Lambda witness at `https://witness.keri.host` responds to OOBI-style URLs with JSON status info instead of the signed CESR reply stream mandated by the KERI specification (see `~/KERI/code/keri-claude/scripts/markdown/keri-specification.md` lines 6394–7070). A KERI agent that resolves `https://witness.keri.host/oobi/{aid}/witness/{eid}` today receives a text payload it cannot cryptographically verify — which means no other KERI agent can actually use our witness.

This phase makes the OOBI endpoints **spec-compliant** so third-party agents can bootstrap trust.

## Problem statement

A proper KERI OOBI response must:

1. Be delivered as **`Content-Type: application/cesr`** (binary CESR stream, not JSON)
2. Contain **three kinds of signed reply messages**, concatenated:
   - KEL replay for the resolved AID (inception + any rotations, with controller signatures)
   - A `/loc/scheme` reply message — binds `{eid, scheme, url}`, signed by the endpoint's own key
   - An `/end/role/add` reply message — binds `{cid, role, eid}`, signed by the authorizing controller
3. Include the `KERI-AID: {aid}` header
4. Match the HTTP status code semantics of the reference `OOBIEnd` handler (200 / 404 / 406)

The current implementation:
- Returns JSON — fails requirements 1, 2, 3
- Never registers its own URL in the reply database — even if it switched to CESR, `hab.replyToOobi()` would return only the KEL because `db.locs` / `db.ends` are empty

## Approach (approved)

**Mirror the reference `kli witness` bootstrap flow using existing keripy machinery.** No new protocol code. Two substantive changes to `witness_handler.py`, one env var, one template tweak.

Why this approach over alternatives:
- **Not "check-then-register":** extra branching logic and silent-no-propagate behavior when URL changes
- **Not "pre-bake `conf.json`":** we already hit filesystem read-only issues in Lambda; adding another filesystem dependency invites fragility
- **This approach uses code paths already exercised by `kli witness`** — `habbing.py:1200-1217` does exactly this during Habery reconfigure when a config file contains `curls`

## Architecture

### Files touched

| File | Change | Lines |
|------|--------|-------|
| `sam-witness/template.yaml` | Add `WITNESS_URL` env var + API `BinaryMediaTypes` | ~5 |
| `sam-witness/env.json` | Placeholder `WITNESS_URL` for local testing | 1 |
| `sam-witness/witness_handler.py` | URL self-registration in `init()` + CESR response in `handle_oobi_get` | ~100 |

### Component: cold-start URL registration

After `_hab = _hby.makeHab(...)` (or reload via `habByName`) in `init()`, register the witness's own URL and controller-role authorization, then parse through `Kevery` reply routes to persist into `db.rpys` / `db.scgs` / `db.lans` / `db.ends` / `db.eans`:

```python
from keri.kering import Roles, Schemes
from keri.help import helping

witness_url = os.environ.get("WITNESS_URL", "").strip()
if witness_url:
    scheme = Schemes.https if witness_url.startswith("https://") else Schemes.http
    stamp = helping.nowIso8601()
    msgs = bytearray()
    msgs.extend(_hab.makeEndRole(eid=_hab.pre, role=Roles.controller, stamp=stamp))
    msgs.extend(_hab.makeLocScheme(url=witness_url, scheme=scheme, stamp=stamp))
    _hby.psr.parse(ims=msgs)
```

Only `Roles.controller` is self-registered. **Witness-role authorization** arrives externally — controllers listing our AID in their `wits` will POST us `/end/role/add` reply messages naming us as their witness (handled by existing `handle_cesr_ingest`). **Mailbox-role authorization** is deferred to Phase 3, which will reuse this same pattern with `role=Roles.mailbox`.

BADA monotonicity (spec lines 6740–6768) makes re-registration on every cold start safe: `helping.nowIso8601()` produces a stamp strictly newer than the previous one, so `db.lans.pin()` / `db.ends.pin()` / `db.locs.pin()` / `db.rpys.pin()` overwrite cleanly. A small handful of DynamoDB writes per cold start — negligible given cold starts occur rarely.

### Component: CESR-producing OOBI handler

Rewrite `handle_oobi_get()` to mirror `src/keri/end/ending.py:558-617`:

```python
from keri.kering import Roles

def handle_oobi_get(event):
    path = event.get("path", "/oobi")
    parts = [p for p in path.split("/") if p and p != "oobi"]
    aid  = parts[0] if parts else _hab.pre
    role = parts[1] if len(parts) > 1 else None
    eid  = parts[2] if len(parts) > 2 else None

    if aid not in _hby.kevers:
        return response(404, {"error": f"unknown aid: {aid}"})

    kever = _hby.kevers[aid]
    if not _hby.db.fullyWitnessed(kever.serder):
        return response(404, {"error": "not fully witnessed"})

    owits = set(kever.wits)
    if aid not in _hby.prefixes and not owits.intersection(_hby.prefixes):
        return response(406, {"error": "not acceptable"})

    eids = [eid] if eid else []
    msgs = _hab.replyToOobi(aid=aid, role=role, eids=eids)
    if not msgs and role is None:
        msgs = _hab.replyToOobi(aid=aid, role=Roles.witness, eids=eids)
        msgs.extend(_hab.replay(aid))

    if not msgs:
        return response(404, {"error": "no oobi content available"})

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/cesr", "KERI-AID": aid},
        "body": base64.b64encode(bytes(msgs)).decode("utf-8"),
        "isBase64Encoded": True,
    }
```

### Component: template.yaml additions

```yaml
Globals:
  Api:
    BinaryMediaTypes:
      - application/cesr
      - "*/*"

# and in WitnessFunction.Environment.Variables:
      WITNESS_URL: !Sub "https://${DomainName}"
```

The `BinaryMediaTypes` block makes API Gateway pass `application/cesr` payloads through as binary (decoding the base64 body from the Lambda response) instead of treating them as text.

## Data flow

**Cold start:**
```
Habery.__init__
  └─> Manager created, Signator hab created, witness hab created
      └─> [new] makeEndRole(eid=pre, role=controller) + makeLocScheme(url, https)
          └─> psr.parse(ims=msgs)
              └─> Kevery.processReplyEndRole  → db.ends.pin, db.eans.pin, db.rpys.pin
              └─> Kevery.processReplyLocScheme → db.locs.pin, db.lans.pin, db.rpys.pin
```

**OOBI request `GET /oobi/{aid}`:**
```
API Gateway → handler(event) → handle_oobi_get
  └─> hab.replyToOobi(aid, role=None) → replyEndRole(cid=aid, role=None)
      ├─> replay(cid)                              # KEL events
      └─> for each (cid,role,eid) in db.ends:      # controller-role record exists
            loadLocScheme(eid, scheme=)             # reads db.rpys by SAID via db.lans
            loadEndRole(cid, eid, role)             # reads db.rpys by SAID via db.eans
  └─> base64(msgs)
  └─> 200 + Content-Type: application/cesr + KERI-AID header
```

## Error handling

| Condition | Code | Body |
|-----------|------|------|
| AID present, fully witnessed, authorized, msgs produced | 200 | CESR stream |
| AID not in `_hby.kevers` | 404 | `{"error": "unknown aid: ..."}` |
| AID present but witness threshold not met | 404 | `{"error": "not fully witnessed"}` |
| AID present but we're not its controller/witness | 406 | `{"error": "not acceptable"}` |
| Valid request but `replyToOobi` returned empty | 404 | `{"error": "no oobi content available"}` |
| `WITNESS_URL` unset at init | — | Skip registration; log warning. Status endpoint still works; OOBI responses lack `/loc/scheme`. |

## Testing

**Layer 1 — regression guard:**
```bash
python3 -m pytest tests/app/test_lambding.py tests/db/test_dynamodbing.py -q
# Expect: 98 passed. No keripy protocol code touched; witness_handler.py only.
```

**Layer 2 — local SAM invocation:**
```bash
python3 -c "import boto3; c=boto3.client('dynamodb',region_name='us-west-2',endpoint_url='http://localhost:8000',aws_access_key_id='fake',aws_secret_access_key='fake'); [c.delete_table(TableName=t) for t in c.list_tables()['TableNames']]"

sam build --template sam-witness/template.yaml --use-container
docker tag witnessfunction:latest witness-handler:latest
sam local invoke WitnessFunction --template sam-witness/template.yaml \
    --env-vars sam-witness/env.json --event sam-witness/events/status-get.json
# Expect: 200 + JSON status. No crash during makeLocScheme / psr.parse.
```

**Layer 3 — live OOBI round-trip (post-deploy):**
```bash
sam deploy --template-file .aws-sam/build/template.yaml \
    --stack-name serverless-witness --region us-east-1 --profile personal \
    --capabilities CAPABILITY_IAM --resolve-image-repos --resolve-s3 \
    --no-confirm-changeset

curl -sI https://witness.keri.host/oobi
# Expect: Content-Type: application/cesr, KERI-AID: B...

curl -s https://witness.keri.host/oobi > /tmp/oobi.cesr
file /tmp/oobi.cesr          # "data" (binary), not "ASCII text"
```

**Layer 3 acceptance — parse round-trip:**
```python
# A fresh local Habery parses the captured bytes; Kevery accepts KEL, stores replies
from keri.db.dynamodbing import DynamoDBer
from keri.app.lambding import BASER_STORES, KEEPER_STORES, setup_baser, setup_keeper
from keri.app.habbing import Habery
from keri.core.signing import Salter
import os

os.environ['AWS_ACCESS_KEY_ID']='fake'; os.environ['AWS_SECRET_ACCESS_KEY']='fake'
kwa = dict(region='us-west-2', endpoint_url='http://localhost:8000')
db = setup_baser(DynamoDBer.open(name='verify-db', stores=BASER_STORES,
                                 table_name='verify-db', clear=True, **kwa))
ks = setup_keeper(DynamoDBer.open(name='verify-ks', stores=KEEPER_STORES,
                                  table_name='verify-ks', clear=True, **kwa))
hby = Habery(name='verify', temp=False, free=True, db=db, ks=ks, salt=Salter().qb64)

with open('/tmp/oobi.cesr', 'rb') as f:
    hby.psr.parse(ims=bytearray(f.read()))

# The witness's AID is now in our local kevers AND its URL is in db.locs
witness_pre = next(k for k in hby.kevers
                   if k != hby.habByName('verify').pre)
loc = hby.db.locs.get(keys=(witness_pre, 'https'))
assert loc.url == 'https://witness.keri.host', f"got {loc.url!r}"
print('OOBI round-trip OK:', witness_pre, '→', loc.url)
```

**Success criterion:** `loc.url` equals `https://witness.keri.host`. If it does, a third-party KERI agent can bootstrap trust in our witness via OOBI.

## Failure modes to catch during review

- `WITNESS_URL` unset in production (template should always populate it from `DomainName`)
- 500 instead of 404 on `/oobi/{unknown-aid}`
- CESR body delivered as ASCII text (symptom: `file` reports "ASCII text", body starts with a base64-looking string) → `BinaryMediaTypes` misconfigured
- Regression of `GET /` status endpoint after handler refactor
- Exception during init if `psr.parse` fails (should log + keep serving, not crash cold start)

## Existing functions reused (no new protocol code)

| Function | Location | Role |
|----------|----------|------|
| `Hab.makeEndRole` | `src/keri/app/habbing.py:1991` | Build signed `/end/role/add` reply |
| `Hab.makeLocScheme` | `src/keri/app/habbing.py:2057` | Build signed `/loc/scheme` reply |
| `Hab.replyToOobi` | `src/keri/app/habbing.py:2212` | Produce OOBI CESR stream |
| `Hab.replyEndRole` | `src/keri/app/habbing.py:2147` | Compose KEL + loc + end replies |
| `Hab.replay` | `src/keri/app/habbing.py:1671` | KEL event replay |
| `Hab.endorse` | `src/keri/app/habbing.py:1493` | Attach Cigar sigs (non-trans) |
| `Parser.parse` (via `_hby.psr`) | — | Route replies to Kevery |
| `Kevery.processReplyLocScheme` | `src/keri/core/eventing.py:4734` | Store `/loc/scheme` |
| `Kevery.processReplyEndRole` | `src/keri/core/eventing.py:4638` | Store `/end/role/add` |
| `Baser.fullyWitnessed` | `src/keri/db/basing.py:1897` | Witness threshold check |
| `helping.nowIso8601` | `keri.help` | BADA-compliant datetime stamp |
| `Roles`, `Schemes` | `src/keri/kering.py:373,377` | Namedtuple constants |

## Out of scope (belongs in later phases)

- Receipt generation edge cases — Phase 2
- Mailbox role authorization registration — Phase 3 adds `makeEndRole(role=mailbox)` additively
- Watcher/registrar role support — out of Lambda-stack scope entirely
- Replacing `_clear_keeper` workaround with transactional init — Phase 4
