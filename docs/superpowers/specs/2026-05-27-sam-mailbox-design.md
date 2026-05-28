# `sam-mailbox` — Standalone KERI Mailbox Service Design

**Status:** Approved for planning
**Date:** 2026-05-27
**Author:** Joseph Lee Hunsaker (with Claude)
**Companion stack:** `sam-witness/`

## Background

The current `sam-witness/` stack runs a KERI witness on AWS Lambda + DynamoDB at
`witness.keri.host`. By inheritance from the keripy reference
`setupWitness(hby, alias, mbx=...)` pattern, that single Lambda also bundles a
`Mailboxer` and accepts `/fwd` exn deposits + `qry r=/mbx` polls. It advertises
both the `witness` and `mailbox` roles on its own AID.

In KERI v2 the `witness` and `mailbox` roles are protocol-independent and
expected to be served by distinct AIDs. We are splitting them: the existing
witness becomes receipt-only, and a new `sam-mailbox/` stack stands up at
`mailbox.keri.host` with its own AID, its own DynamoDB tables, and a true
streaming SSE long-poll surface for `qry r=/mbx`.

The codebase is greenfield — no production controllers depend on the current
bundled behavior — so the split can be clean rather than backward-compatible.

## Goals

- New `sam-mailbox/` stack: independent AID, independent storage, lives at
  `mailbox.keri.host`.
- Mailbox AID is witnessed by `witness.keri.host` (not self-witnessed), so its
  KEL is anchored in the same trust fabric as any other controller.
- Polling uses **real SSE long-poll** via AWS API Gateway response streaming
  (Nov 2025 feature for REST API GW), not the witness's current one-shot
  buffered drain.
- Open relay v1: any AID can deposit `/fwd` for any destination AID (signature
  verification gates the operation, no allow-list).
- Strip mailbox surface from `sam-witness/` cleanly — no compatibility shims.

## Non-goals (v1)

- Per-AID rate limiting / quotas — deferred.
- Allow-list of registered AIDs (controllers who anchored their mailbox role
  here). Deferred to a follow-up once usage patterns are understood.
- Multi-witness redundancy for the mailbox AID. Single witness is fine for
  greenfield; rotation/expansion is a future operation.
- Cross-witness fail-over for receipt issuance during mailbox cold start.

## Architecture

Two independent SAM stacks in this repo, each its own deployment lifecycle:

```
sam-witness/   (existing, gets a strip)
  Domain:   witness.keri.host
  Tables:   witness-db, witness-ks
  AID:      BNbRfMPQQge6wKO0uof9Y0e_QYOfiF08k9drc6pOgzjt (existing, unchanged)
  Role:     witness only (receipts, OOBI, KEL query)

sam-mailbox/   (NEW)
  Domain:   mailbox.keri.host
  Tables:   mailbox-db, mailbox-ks
  AID:      newly incepted, non-transferable, witnessed by sam-witness AID
  Role:     mailbox only (deposit /fwd exn, poll qry r=/mbx as SSE, OOBI)
```

Each stack has independent: KERI AID, DynamoDB tables, ACM cert, Route53 A
record, IAM role (least privilege), CloudWatch log group, SAM lifecycle.

**Shared between them**: `src/keri/app/lambding.py` — the DynamoDBer ↔
Baser/Keeper/Mailboxer wiring already exists and is used by both. Both Lambda
container images build from the same `src/keri/` source.

**No inter-service communication.** The mailbox Lambda never calls the witness
at runtime; the witness Lambda never calls the mailbox. The controller (a fat
wallet, signify-ts client, or keria agent) is the broker — it talks to its own
witnesses for receipts and to other controllers' mailboxes for messaging. The
sole exception is a one-time witness round-trip during the mailbox's first
cold-start to obtain a receipt on its inception event (see Cold-start init
below).

## Components

### File layout — `sam-mailbox/`

```
sam-mailbox/
├── template.yaml          # SAM stack: DynamoDB, Lambda (streaming), API GW, ACM, Route53
├── samconfig.toml         # stack_name=serverless-mailbox, region=us-east-1, profile=personal
├── Dockerfile             # python:3.14-slim + libsodium + awslambdaric
├── Makefile               # build / deploy / test-live convenience targets
├── requirements.txt       # lmdb, pysodium, hio, falcon, boto3, etc.
├── bootstrap.py           # Lambda runtime entry; imports mailbox_handler
├── mailbox_handler.py     # init(), handler(event, context), route dispatch
├── env.json               # local SAM CLI envs for `sam local invoke`
├── events/
│   ├── fwd-post.json
│   ├── mbx-qry-post.json
│   ├── oobi-get.json
│   └── status-get.json
├── lib/                   # libsodium shared libs (mirrored from sam-witness)
├── test_live.py           # pytest hitting deployed mailbox.keri.host
└── test_live.sh           # bash smoke runner
```

### `mailbox_handler.py` — module surface

| Function | Route | Purpose |
|---|---|---|
| `init()` | (cold-start) | Open DynamoDBer for Baser + Mailboxer + Keeper, create/load mailbox Hab (non-transferable, witnessed by `sam-witness` AID), publish self-rpy (controller + mailbox roles + `/loc/scheme` for `https://mailbox.keri.host`), register `ForwardHandler(hby, mbx=hby.db)` |
| `handler(event, context)` | Lambda entry | Route by path+method, exception-wrap, return API GW response |
| `handle_cesr_ingest()` | `POST /` | Parse CESR via `_hby.psr.parse(framed=True)`. Peek for `qry r=/mbx` → open response stream + drive long-poll. Otherwise 204 (deposit landed in mbx via ForwardHandler). |
| `handle_oobi_get()` | `GET /oobi/*` | Self-OOBI for the mailbox AID only. Same logic as witness's `handle_oobi_get` but only this Hab's pre is valid. |
| `handle_status()` | `GET /` | `{mailbox: <AID>, alias, sn, queue_count}` |

**Deliberately NOT present** (vs witness): `handle_receipt_post`,
`handle_receipt_get`, `handle_query_get`, `_drain_receipt_cues`, `Reger`,
`Verifier`. The mailbox does not produce receipts or serve KEL queries.

### Shared helpers — `src/keri/app/lambding.py`

Existing module, unchanged for this work. Both witness and mailbox import:
- `BASER_STORES`, `KEEPER_STORES`, `MAILBOXER_STORES` (constants)
- `setup_baser(dber)`, `setup_keeper(dber)`, `setup_mailboxer(dber)` (attach
  sub-databases and business methods onto a DynamoDBer instance)

### Changes to `sam-witness/`

- `init()` — drop `ForwardHandler` registration; drop `Roles.mailbox`
  advertisement; drop `setup_mailboxer(db)` call; remove `MAILBOXER_STORES`
  from `baser_and_mbx_stores` (now just `BASER_STORES`).
- `handle_cesr_ingest()` — drop the `_detect_mbx_query` peek and the SSE
  response branch. Remove `_format_sse_events` (moved to mailbox).
- Tests that exercise mbx behavior in `sam-witness/test_live.py` are migrated
  to `sam-mailbox/test_live.py`.

### SAM template — key resources

- `MailboxBaserTable` — DynamoDB, `${MailboxName}-db`, same key schema and GSI
  as the witness pattern.
- `MailboxKeeperTable` — DynamoDB, `${MailboxName}-ks`.
- `MailboxFunction` — `AWS::Serverless::Function`, container image, ARM64,
  1024 MB memory, timeout ≤ 870s (well under the 15-min streaming cap),
  **`InvokeMode: RESPONSE_STREAM`** (or CFN equivalent).
- `MailboxCertificate`, `MailboxApiDomainName`, `MailboxBasePathMapping`,
  `MailboxDnsRecord` — ACM + Route53 wiring for `mailbox.keri.host`, same
  pattern as witness.
- API GW integration URI uses the **response-streaming variant**.

### Parameters (deploy-time)

| Parameter | Default | Purpose |
|---|---|---|
| `MailboxName` | `mailbox` | DynamoDB table prefix |
| `MailboxAlias` | `mailbox` | Hab alias |
| `MailboxSalt` | (required, no default) | qb64-encoded salt for deterministic AID across cold starts |
| `DomainName` | `mailbox.keri.host` | Custom domain |
| `HostedZoneId` | `Z0070723WLKQKTOACN5H` | Route53 hosted zone |
| `WitnessAid` | (required) | AID of `witness.keri.host` (the witness for this mailbox AID) |
| `WitnessUrl` | `https://witness.keri.host` | Base URL of the witness service (Lambda constructs the OOBI URL as `{WitnessUrl}/oobi/{WitnessAid}/controller` and the receipt URL as `{WitnessUrl}/receipts`) |

## Data flow

### Flow 1 — Deposit (sender → mailbox)

```
Sender wallet (Alice)                Mailbox Lambda                DynamoDB
─────────────────────                ──────────────                ────────
1. Construct exn:
   { r: "/fwd",
     a: { src: Alice, dest: Bob, topic: "credential" },
     e: { fwd: <inner-msg> } }
2. Sign with Alice's keys.
3. POST https://mailbox.keri.host/   ──►  handle_cesr_ingest:
   Content-Type: application/cesr           a. _extract_cesr_stream(event)
   Body: exn + attachments                  b. _detect_mbx_query → None
                                            c. _hby.psr.parse(ims, framed=True)
                                               • Kevery sees no events
                                               • Exchanger dispatches /fwd exn to
                                                 ForwardHandler(hby, mbx=hby.db)
                                               • ForwardHandler verifies sender
                                                 sig + calls mbx.storeMsg(
                                                   topic=f"{dest}/{topic}",
                                                   msg=inner_msg)
                                                                      ──►  tpcs.<dest/topic>
                                                                           msgs.<digest>
                                            d. return 204 No Content
```

The mailbox never sees `dest`'s keys; it stores raw bytes under a topic key
derived from `dest`. Sender's KEL is learned by the parser as a side effect of
signature verification (standard keripy parser behavior).

### Flow 2 — Poll (recipient → mailbox, SSE long-poll)

```
Recipient wallet (Bob)               Mailbox Lambda                DynamoDB
──────────────────────               ──────────────                ────────
1. Construct qry:
   { t: "qry", r: "/mbx",
     q: { pre: Bob,
          topics: { credential: 0, receipt: 0 } } }
2. Sign with Bob's keys.
3. POST https://mailbox.keri.host/   ──►  handle_cesr_ingest:
   Content-Type: application/cesr           a. _extract_cesr_stream(event)
   Body: qry + sig                          b. _detect_mbx_query → serder
                                            c. _hby.psr.parse(ims, framed=True)
                                               • Kevery verifies qry signature
                                                 against Bob's KEL                    ◄── tpcs/msgs read
                                            d. Open response stream
                                            e. For each (name, last_on) in q.topics:
                                                  cloneTopicIter(
                                                     topic=f"{pre}/{name}",
                                                     fn=last_on+1):
                                                yield "id: <on>\nevent: <name>\n
                                                       retry: 5000\ndata: <msg>\n\n"
                                            f. Hold connection open (long-poll):
                                               • flush keepalive comment frame
                                                 (`:keepalive\n\n`) every ~4 min
                                                 to stay under the 5-min idle ceiling
                                               • when new msg arrives in topic,
                                                 stream it as next SSE event
                                            g. Connection closes at idle ceiling
                                               or 15-min hard cap; client
                                               reconnects per retry: 5000.
```

The key difference from the witness's current pattern is step (f) — the
streaming hold. In the current witness, step (e) drains whatever exists at the
moment of the POST and immediately closes; new arrivals require the client to
re-poll. The streaming version pushes new messages within the 5-min window.

### Flow 3 — OOBI (any caller → mailbox)

```
Caller                               Mailbox Lambda
──────                               ──────────────
1. GET https://mailbox.keri.host/oobi/<mailbox-AID>/mailbox
                                  ──► handle_oobi_get:
                                      a. Parse path → aid=mailbox-AID, role=mailbox
                                      b. Verify aid in _hby.kevers
                                      c. Verify db.fullyWitnessed(kever.serder)
                                         (the receipt in db.wigs from cold-start
                                         init makes this pass)
                                      d. _hab.replyToOobi(aid, role) → signed CESR:
                                         • /end/role/add (cid=mailbox, role=mailbox,
                                           eid=mailbox)
                                         • /loc/scheme (url=https://mailbox.keri.host)
                                         • mailbox KEL
                                      e. Return Content-Type: application/cesr
```

The mailbox only serves OOBI for its own AID. OOBI requests for any other AID
return 404.

### Flow 4 — Cold-start init

```
Lambda runtime ──► init():
                   a. DynamoDBer.open(table=mailbox-db,
                                      stores=BASER_STORES + MAILBOXER_STORES)
                      → setup_baser(db); setup_mailboxer(db)
                   b. DynamoDBer.open(table=mailbox-ks, stores=KEEPER_STORES)
                      → setup_keeper(ks)
                   c. Partial-init recovery: if keeper has `pidx` but baser
                      lacks `__signatory__`, clear keeper and retry. Mirrors
                      existing witness handler logic.
                   d. Habery(db=db, ks=ks, salt=MAILBOX_SALT, ...)
                   e. _hab = hby.habByName("mailbox") or hby.makeHab(
                         transferable=False,
                         isith='1', icount=1, ncount=0, nsith='0',
                         wits=[WITNESS_AID], toad=1)
                   f. hby.prefixes.add(_hab.pre)
                   g. If db.wigs has no receipt for _hab.kever.serder.said:
                        # one-time witness round-trip on fresh inception
                        - If WITNESS_AID not in _hby.kevers:
                            GET {WITNESS_URL}/oobi/{WITNESS_AID}/controller
                            → psr.parse(response)
                        - POST {WITNESS_URL}/receipts
                            body=hab.makeOwnEvent(sn=0)
                            content-type=application/cesr
                        - psr.parse(receipt_response) → lands in db.wigs
                        - assert _hby.db.fullyWitnessed(_hab.kever.serder)
                   h. Publish self-rpy: makeEndRole(role=controller),
                      makeEndRole(role=mailbox), makeLocScheme(url=https://mailbox.keri.host)
                      → psr.parse(url_msgs) — stores in db.rpys/ends/locs
                   i. hby.exc.addHandler(ForwardHandler(hby, mbx=hby.db))
                   j. Keep _hby, _hab, _parser as module globals (warm reuse).
```

Step (g) runs exactly once in the mailbox's lifetime. After the first
successful inception+receipt cycle, db.wigs has the entry and the branch is
skipped on every subsequent cold start.

## Error handling

### Per-request errors

| Condition | Response |
|---|---|
| Empty body on `POST /` | `400 {error: "empty body"}` |
| Malformed CESR / parser raises | `400 {error: <parser msg>}` (logged with stack trace) |
| Signature verification fails (deposit or poll) | `401 {error: "signature invalid"}` (logged at WARNING with offending AID) |
| `qry r=/mbx` missing `q.pre` or `q.topics` | `400 {error: "qry/mbx requires q.pre (str) and q.topics (dict)"}` |
| Polling AID with no KEL known to mailbox | `401 {error: "unknown identifier"}` |
| `GET /oobi/<aid>/...` for non-mailbox AID | `404 {error: "unknown aid"}` |
| `GET /oobi` while `fullyWitnessed` is false | `404 {error: "not fully witnessed"}` (should never happen post-init; logged at ERROR if it does) |
| Any other unhandled exception | `500 {error: <message>}` |

### Streaming-specific

| Condition | Handling |
|---|---|
| Lambda runtime times out mid-stream | Runtime closes connection cleanly; client reconnects per `retry: 5000` |
| 5-min idle ceiling approached | Handler emits `:keepalive\n\n` SSE comment every ~4 min |
| Client disconnects mid-stream | Catch `GeneratorExit` / write failure silently (no stack trace for normal close) |
| Stream produces no events | Keepalives only, connection closes at idle ceiling, client reconnects |
| Backpressure | Trust API GW + Lambda runtime to handle; handler just yields |

### Cold-start failures

| Condition | Handling |
|---|---|
| Witness unreachable on first inception | `init()` raises → 500. No partial state written (inception isn't persisted until receipt back). Next invocation retries cleanly. |
| Witness returns malformed/unsigned receipt | `psr.parse` raises → init aborts. Same as above. |
| Partial init (keeper has `pidx`, baser lacks `__signatory__`) | `_clear_keeper(ks)` recovery path (mirrors existing witness logic) |
| `MAILBOX_SALT` env var missing | Refuse to start — raise `ConfigurationError`. Missing salt means non-recoverable AID; safer to fail loud. |
| DynamoDB unavailable | boto3 default retry policy; if exhausted, init raises → 500 → next invocation retries. |

### Deferred (not v1)

- Per-AID rate limiting / quotas
- Allow-list of registered AIDs
- Multi-witness redundancy for mailbox AID
- Concurrent-connection ceiling protection beyond Lambda's default (1000)

## Operational constraints

- Each open SSE poll connection holds one Lambda instance for up to 5 min.
  Default account concurrency cap is 1000 in us-east-1. >1000 simultaneous
  pollers → throttling (429 from API GW). Out-of-scope for v1; flag for
  follow-up.
- First-deploy depends on witness being live (one-shot dependency only). After
  initial inception, mailbox runs without witness coupling.
- Witness AID rotation requires a mailbox rotation event to update its `b:`
  field. Out of v1 scope; would be a manual `kli rotate` operation.

## Testing

### Tier 1 — Unit (fast, every change)

Co-located with handler or in `tests/sam-mailbox/`. No AWS, no DynamoDB.

- `_extract_cesr_stream`: body-only, body + CESR-ATTACHMENT header,
  `-V`/`-C` wrapped attachments, base64-encoded body.
- `_detect_mbx_query`: valid qry, valid qry without leading slash, non-qry,
  malformed bytes, empty.
- `_format_sse_events`: multi-topic, empty queue, missing topic, ordinal
  advancement, message ordering.
- `_keepalive_loop`: emits comment frames at configured interval; stops on
  close.
- Init partial-state recovery exercises `_clear_keeper` path.

### Tier 2 — Local integration (sam local + DynamoDB Local + mock witness)

- Cold-start with mocked witness returning a real signed receipt; verify
  `db.wigs` populated, `fullyWitnessed` passes.
- Deposit → store: POST `/fwd` exn fixture, verify `tpcs.` and `msgs.`
  populated under expected topic key.
- Poll drain: POST `qry r=/mbx`, verify SSE body framing.
- Self-OOBI: parse returned CESR, verify rpy messages signed by mailbox AID.
- Status: GET `/`, verify shape.

(Sam local cannot test real streaming integration — it speaks buffered
request/response only. Streaming behavior is covered in Tier 3.)

### Tier 3 — Live deployment (`test_live.py`)

Runs against deployed `mailbox.keri.host` after `sam deploy`.

- `test_get_root_returns_mailbox_aid`
- `test_get_oobi_returns_signed_cesr`
- `test_oobi_advertises_mailbox_role`
- `test_fwd_post_stores_message_for_recipient`
- `test_mbx_query_returns_queued_messages`
- `test_mbx_query_resumes_from_last_ordinal`
- `test_mbx_query_missing_q_pre_returns_400`
- `test_streaming_holds_connection_open` — open SSE poll, deposit from another
  connection after 30s, verify event arrives on the held stream within ~1s.
- `test_streaming_keepalive_emitted` — observe `:keepalive` frame between
  regular events.

### Tier 4 — Witness regression (added to `sam-witness/test_live.py`)

- `test_witness_oobi_no_longer_advertises_mailbox_role`
- `test_witness_fwd_post_returns_not_handled` (response shape resolved under
  Open Question 2 — 204 or 400)
- `test_witness_mbx_query_returns_400_or_404`
- All existing receipt + OOBI tests continue to pass.

### Hard-to-test scenarios (manual runbook)

- 5-min idle keepalive timing (CI can't wait 4+ min realistically).
- >1000 concurrent SSE connections (load test, out of v1 scope).
- First-deploy cold-start (happens once per stack lifetime; verify during
  initial deploy of each environment).

### Test fixtures

Live tests need a test-controller AID with a known salt for signing deposits
and polls. Use the witness's existing pattern — generate a deterministic test
Hab from a hardcoded test salt, throwaway and per-test-run only.

## Open questions for the planning phase

These were intentionally not decided during brainstorming and need to be nailed
down before implementation begins:

1. **Python Lambda response-streaming runtime** — direct `awslambdaric`
   streaming generator vs AWS Lambda Web Adapter (Falcon app inside container).
   Direct streaming is more Python-Lambda-native; the Web Adapter brings the
   handler shape closer to keripy's existing `indirecting.HttpEnd`. Decide
   based on:
   - Maturity of Python streaming runtime API for our `python:3.14-slim` base
   - How clean the generator/yield pattern looks for our `_format_sse_events`
     loop with embedded keepalives
   - Whether the Web Adapter sidecar adds meaningful cold-start cost

2. **`POST /fwd` on the stripped witness** — return 204 (silent ignore) or 400
   (reject as unsupported)? 400 is more honest; 204 is more permissive during
   the very-short transition window. Greenfield argues for 400.

3. **Witness AID injection mechanism** — SAM template Parameter (set at
   `sam deploy` time, hard-coded) vs CFN cross-stack import from
   `serverless-witness` stack outputs. Parameter is simpler; cross-stack import
   automates the dependency. Default to Parameter for v1.

## Deliverables summary

- New: `sam-mailbox/` directory with the full set of files listed above.
- Modified: `sam-witness/witness_handler.py` and `sam-witness/template.yaml` to
  remove mailbox surface.
- Modified: `sam-witness/test_live.py` for regression coverage of the strip.
- No changes to `src/keri/app/lambding.py` — already has what we need.

## Streaming runtime resolution

Resolves **Open Question 1** (Python Lambda response-streaming runtime,
direct `awslambdaric` vs AWS Lambda Web Adapter). Findings as of 2026-05-27
from AWS docs and the awslambdaric / aws-lambda-web-adapter repos.

### Decision: AWS Lambda Web Adapter (LWA) in `RESPONSE_STREAM` mode

We use **AWS Lambda Web Adapter** sidecar in front of a Falcon/uvicorn-style
HTTP app inside the `python:3.14-slim` container. The Python app exposes a
plain HTTP/1.1 server on `localhost:8080` and emits a normal streaming HTTP
response (`Transfer-Encoding: chunked`); LWA reformats it into the API
Gateway response-streaming wire format (metadata JSON + 8 null-byte
delimiter + chunked payload) and posts it to the Lambda Runtime API's
streaming `/response` endpoint.

### Rationale (why not direct `awslambdaric`)

- **AWS docs are explicit:** "Lambda supports response streaming on Node.js
  managed runtimes. For other languages, including Python, you can use a
  custom runtime with a custom Runtime API integration to stream responses
  or use the Lambda Web Adapter."
  ([configuration-response-streaming.html](https://docs.aws.amazon.com/lambda/latest/dg/configuration-response-streaming.html))
- `awslambdaric` (latest 4.0.0, 3.1.1 stable May 2025) **has no
  `streamifyResponse`-equivalent decorator and no public generator-based
  streaming API.** There is no Python equivalent of
  `awslambda.streamifyResponse(...)`. Hand-rolling a custom Runtime API
  client that speaks `Lambda-Runtime-Function-Response-Mode: streaming`
  with chunked transfer encoding to `/runtime/invocation/AwsRequestId/response`
  is technically possible but is exactly the "custom Runtime API
  integration" the AWS docs point at — significant scope for v1.
- **LWA is officially the supported path** for Python streaming and ships a
  working FastAPI streaming example (`fastapi-response-streaming`). It is a
  Lambda Extension (sidecar), not a runtime replacement — it composes with
  `awslambdaric` cleanly and supports non-AWS base images including
  `python:3.x-slim`.
- LWA handles the API GW response-streaming wire format (metadata JSON
  prelude + `\x00 * 8` delimiter + chunked body) **transparently**: the
  Python app just writes a normal streaming HTTP response and LWA does the
  framing. This is critical for our SSE use case — we can yield
  `data: <cesr>\n\n` and `:keepalive\n\n` frames as ordinary HTTP chunks
  without manually constructing the API GW prelude.
- Cold-start cost of the LWA extension is small (single Rust binary
  ~10 MB, multi-arch). Acceptable for a long-poll workload where cold
  starts are amortized over multi-minute connections.

### Approach: Falcon ASGI/WSGI app behind LWA

The handler is an HTTP server, not a Lambda `(event, context)` callable.
`bootstrap.py` starts a uvicorn (or `falcon`-native gunicorn) server on
`AWS_LWA_PORT=8080` and registers Falcon resources for `/`, `/oobi/...`,
`/`, `POST /` (CESR ingest), and `POST /` query path that maps to
`qry r=/mbx`. The SSE long-poll endpoint is a Falcon resource whose
`on_post` method returns a streaming response (e.g. via Falcon's
`resp.stream = generator(...)`) emitting `data:`/`:keepalive` frames.

This conveniently matches keripy's existing `indirecting.HttpEnd` Falcon
route shape — we are not inventing new handler conventions, we are reusing
the same Falcon plumbing the reference implementation already uses for
witness/mailbox endpoints.

### Exact handler signature

There is **no `def handler(event, context)`** for the streaming path.
Instead:

```python
# bootstrap.py (entrypoint, run by Docker CMD)
import falcon
import uvicorn
from mailbox_handler import build_app

app = build_app()  # returns a falcon.asgi.App with all routes mounted

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
```

```python
# mailbox_handler.py — SSE poll resource
class MbxPollResource:
    async def on_post(self, req, resp):
        body = await req.bounded_stream.read()
        # ... parse CESR qry r=/mbx, look up cursor, etc. ...
        resp.content_type = "text/event-stream"
        resp.set_header("Cache-Control", "no-cache")
        resp.set_header("X-Accel-Buffering", "no")
        resp.stream = self._sse_generator(pre, topic, cursor, deadline)

    async def _sse_generator(self, pre, topic, cursor, deadline):
        while time.time() < deadline:
            for evt in mailboxer.cloneIter(pre=pre, topic=topic, fn=cursor):
                yield format_sse_event(evt).encode("utf-8")
                cursor = evt.fn + 1
            yield b":keepalive\n\n"
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
```

The buffered endpoints (`GET /oobi/...`, `POST /` CESR ingest,
`GET /status`) are also Falcon resources with normal (non-streaming)
responses; LWA handles both modes through the same `RESPONSE_STREAM`
function-level configuration.

### Exact SAM template snippet

```yaml
Resources:
  MailboxFunction:
    Type: AWS::Serverless::Function
    Properties:
      PackageType: Image
      Architectures: [arm64]
      Timeout: 870        # < 15-min streaming cap
      MemorySize: 1024
      Environment:
        Variables:
          AWS_LWA_INVOKE_MODE: response_stream
          AWS_LWA_PORT: "8080"
          AWS_LWA_READINESS_CHECK_PATH: /status
          # ... mailbox-specific env (table names, witness AID, etc.)
    Metadata:
      DockerContext: .
      Dockerfile: Dockerfile

  MailboxApi:
    Type: AWS::ApiGateway::RestApi
    Properties:
      Name: mailbox-api
      EndpointConfiguration:
        Types: [REGIONAL]    # REQUIRED — Edge-optimized caps idle at 30s

  MailboxAnyMethod:
    Type: AWS::ApiGateway::Method
    Properties:
      RestApiId: !Ref MailboxApi
      ResourceId: !GetAtt MailboxApi.RootResourceId
      HttpMethod: ANY
      AuthorizationType: NONE
      Integration:
        Type: AWS_PROXY
        IntegrationHttpMethod: POST
        # NB: /response-streaming-invocations (NOT /invocations).
        # API version date is 2021-11-15 (the streaming-specific date,
        # NOT 2015-03-31 from the buffered proxy integration).
        Uri: !Sub >-
          arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/2021-11-15/functions/${MailboxFunction.Arn}/response-streaming-invocations
```

Companion `Dockerfile` excerpt (the one new line vs the witness image):

```dockerfile
FROM python:3.14-slim
# Lambda Web Adapter as a Lambda Extension
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.0.0 \
     /lambda-adapter /opt/extensions/lambda-adapter
# ... rest of the build (libsodium, pip install, COPY src, etc.) ...
CMD ["python", "bootstrap.py"]
```

### Limits this commits us to

- **REST API Gateway only.** HTTP API does not (yet) support Lambda
  response-streaming integration. Our existing witness pattern is REST
  API, so this is no regression.
- **Regional endpoint required** for the 5-minute idle timeout.
  Edge-optimized caps idle at 30s, which would kill long-poll holds.
- **Function `Timeout` ≤ 900s** (15-min Lambda max for streaming). We pin
  870s to leave headroom.
- **Keepalive cadence < 5 min** to avoid API GW idle close. We will use
  ~25s in implementation, well under the cap.
- **`sam local invoke` cannot exercise the streaming integration** — it
  speaks buffered request/response only. Tier-3 live tests against a
  deployed stack are the only way to verify SSE behavior end-to-end. This
  is already called out in the test plan section.
- **Bandwidth cap after first 6 MB:** 2 MBps. Mailbox payloads are tiny
  CESR frames so this is irrelevant for v1, but worth knowing.

### Feeds into

- **Task 1.4 (`Dockerfile`):** add the LWA `COPY --from=...` line.
- **Task 1.3 (`bootstrap.py`):** boot uvicorn on port 8080 with the
  Falcon app, **not** the `awslambdaric` handler-loop bootstrap used by
  `sam-witness/`.
- **Task 1.7 (`template.yaml`):** use the snippet above — env vars on the
  Function, `RestApi` with `REGIONAL`, integration URI suffix
  `/response-streaming-invocations` at API version `2021-11-15`.
- **Task 2.7 (SSE streaming handler):** implement as a Falcon async
  resource with `resp.stream = async_generator(...)`; no manual API GW
  metadata prelude or null-byte delimiter — LWA handles framing.
