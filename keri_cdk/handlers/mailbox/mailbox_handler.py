"""KERI Mailbox Lambda handler — Falcon ASGI app served via uvicorn behind
AWS Lambda Web Adapter for response-streaming-compatible SSE long-poll.

Cold-start populates module-level singletons _hby, _hab via init(); subsequent
warm invocations reuse them. build_app() wires Falcon routes to Resource
classes and returns the ASGI App that bootstrap.py boots with uvicorn.
"""

import asyncio
import base64
import json
import logging
import os
import sys
import time

# Resolve libsodium BEFORE any keri import (keri imports are deferred into
# init()/build_app()). The shared shim lives one level up at
# keri_cdk/handlers/_libsodium.py; each handler dir is its own Lambda asset, so
# make the parent ``handlers/`` dir importable. Idempotent and a no-op if the
# .so cannot be found (e.g. a host venv that loads libsodium normally).
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
try:
    from _libsodium import ensure_libsodium  # zip/container: parent dir on sys.path
except ImportError:  # pragma: no cover - resolved as a package in the host env
    try:
        from keri_cdk.handlers._libsodium import ensure_libsodium
    except ImportError:
        ensure_libsodium = None
if ensure_libsodium is not None:
    ensure_libsodium()

import falcon
import falcon.asgi

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Module-level singletons (warm across Lambda invocations)
_hby = None
_hab = None
_parser = None
_initialized = False


def _retry_negative(read, *, attempts=4, delay=0.05):
    """Retry a GSI-served read that returns falsy (eventual-consistency lag) up to
    `attempts` times. A truthy result returns immediately; only the not-found path
    retries. Returns the last (possibly falsy) result."""
    import time
    result = read()
    for _ in range(attempts - 1):
        if result:
            return result
        time.sleep(delay)
        result = read()
    return result


def _clear_keeper(ks):
    """Wipe all keeper key material so Habery init can re-incept from scratch.

    Needed on destroy-replace: CloudFormation destroys the Baser table
    (empty on redeploy) but the keeper secret survives with stale key
    material. ks.salt / ks.bran are top-level attrs (the original salt) and
    are NOT cleared — re-incepting from the preserved salt reproduces the
    same non-transferable AID.
    """
    ks._data.clear()
    ks._subdbs.clear()
    ks._flush()


def _ensure_witness_receipt(witness_aid, witness_url):
    """If db.wigs has no receipt for our own kever, do a one-time witness
    round-trip:
      1. Resolve witness OOBI to ingest witness KEL (if not already known).
      2. POST our inception event to witness /receipts.
      3. Parse the receipt response -> lands in db.wigs.

    Raises if witness is unreachable or receipt is invalid. No partial state
    is written; the next cold-start retries cleanly.
    """
    import urllib.request

    kever = _hab.kever
    pre_b = _hab.pre.encode("utf-8")
    said_b = kever.serder.saidb

    if _hby.db.wigs.get(keys=(pre_b, said_b)):
        return  # already receipted

    if witness_aid not in _hby.kevers:
        oobi_url = f"{witness_url}/oobi/{witness_aid}/controller"
        logger.info("fetching witness OOBI %s", oobi_url)
        with urllib.request.urlopen(oobi_url, timeout=10) as r:
            kel_bytes = r.read()
        _hby.psr.parse(ims=bytearray(kel_bytes))
        if witness_aid not in _hby.kevers:
            raise RuntimeError(
                f"witness OOBI parse did not yield kever for {witness_aid}"
            )

    # NOTE: keripy v2 method is msgOwnEvent (NOT makeOwnEvent).
    icp_msg = _hab.msgOwnEvent(sn=0)
    receipts_url = f"{witness_url}/receipts"
    logger.info("posting inception to %s for receipt", receipts_url)
    req = urllib.request.Request(
        receipts_url, data=bytes(icp_msg),
        headers={"Content-Type": "application/cesr"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        receipt_bytes = r.read()
    _hby.psr.parse(ims=bytearray(receipt_bytes))

    if not _hby.db.fullyWitnessed(_hab.kever.serder):
        raise RuntimeError("witness round-trip did not yield a valid receipt")


def _publish_self_endpoints():
    """Publish signed rpy messages advertising the mailbox's own OOBI surface:
    /end/role/add for controller + mailbox roles, /loc/scheme for the
    mailbox URL. BADA monotonicity via nowIso8601 means re-running on every
    cold start is safe.
    """
    from keri.kering import Roles, Schemes
    from keri.help import helping

    mailbox_url = os.environ.get("MAILBOX_URL", "").strip()
    if not mailbox_url:
        logger.warning("MAILBOX_URL not set; OOBI responses will lack /loc/scheme")
        return

    scheme = Schemes.https if mailbox_url.startswith("https://") else Schemes.http
    stamp = helping.nowIso8601()
    msgs = bytearray()
    msgs.extend(_hab.makeEndRole(eid=_hab.pre, role=Roles.controller, stamp=stamp))
    msgs.extend(_hab.makeEndRole(eid=_hab.pre, role=Roles.mailbox, stamp=stamp))
    msgs.extend(_hab.makeLocScheme(url=mailbox_url, scheme=scheme, stamp=stamp))
    try:
        _hby.psr.parse(ims=msgs)
    except Exception as exc:
        logger.warning("failed to register self-endpoints: %s", exc)


def init():
    """Cold-start: set up Habery with DynamoDB, create/load mailbox Hab,
    do one-time witness round-trip on fresh inception, publish self-OOBI rpy,
    register ForwardHandler.

    Called by build_app() at uvicorn startup. Sets module globals _hby,
    _hab, _parser, _initialized.
    """
    global _hby, _hab, _parser, _initialized
    if _initialized:
        return

    from keri.db.dynamodbing import DynamoDBer
    from keri.app.lambding import (
        BASER_STORES, MAILBOXER_STORES,
        setup_baser, setup_keeper, setup_mailboxer,
    )
    from keri.app.habbing import Habery
    from keri.app.configing import Configer

    name = os.environ.get("MAILBOX_NAME", "mailbox")
    alias = os.environ.get("MAILBOX_ALIAS", "mailbox")
    region = os.environ.get("MAILBOX_REGION", "us-east-1")
    endpoint_url = os.environ.get("MAILBOX_ENDPOINT_URL")
    baser_table = os.environ.get("MAILBOX_BASER_TABLE") or f"{name}-db"
    # WITNESS_AID / WITNESS_URL are kept as env vars for forward compatibility
    # but are no longer used at init() time. keripy v2 forbids non-transferable
    # AIDs from declaring witnesses (eventing.py:2230); the mailbox AID is
    # self-anchored (trust via DNS, same model as the witness service).
    # Re-enabling witnessing requires switching the mailbox to a transferable
    # AID with next-key management.

    kwa = dict(region=region)
    if endpoint_url:
        kwa["endpoint_url"] = endpoint_url
        import boto3
        kwa["session"] = boto3.Session(
            aws_access_key_id="fake",
            aws_secret_access_key="fake",
            region_name=region,
        )

    # Baser + Mailboxer share a table (non-overlapping subkeys)
    baser_and_mbx_stores = list(set(BASER_STORES + MAILBOXER_STORES))
    db = DynamoDBer.open(name=name, stores=baser_and_mbx_stores,
                         table_name=baser_table, **kwa)
    setup_baser(db)
    setup_mailboxer(db)

    # Keeper: one KMS-encrypted secret per stack (NOT a DynamoDB -ks table).
    # The secret is a single doc {v, salt, bran, keeper}: salt+bran are stored
    # as plaintext JSON fields (KMS protects the whole secret at rest) and the
    # keeper blob is ADDITIONALLY aeid-encrypted via bran. We get-or-create the
    # secret here at cold start (race-safe CREATE-ONLY), minting a fresh
    # salt+bran on first ever deploy; every later cold start reloads the SAME
    # secret. Because the mailbox AID is non-transferable and salty-derived,
    # re-incepting from the preserved salt always reproduces the same AID —
    # which is what makes destroy-replace (empty Baser, surviving secret) safe.
    from keri.db.secretkeeper import SecretStore, SecretKeeper
    from keri.core.signing import Salter
    keeper_secret = os.environ.get("MAILBOX_KEEPER_SECRET") or f"keri/{name}/keeper"
    secret_endpoint = os.environ.get("MAILBOX_SECRET_ENDPOINT_URL") or None
    store = SecretStore(region=region, endpoint_url=secret_endpoint)
    store.get_or_create(keeper_secret, lambda: json.dumps({
        "v": 1, "salt": Salter().qb64, "bran": Salter().qb64[2:23], "keeper": None,
    }))
    ks = SecretKeeper.open(store=store, secret_name=keeper_secret)
    setup_keeper(ks)

    # Detect partial-init state and recover
    _pidx_raw = ks.gbls.get("pidx")
    _signatory_pre = db.hbys.get("__signatory__")
    if _pidx_raw is not None and _signatory_pre is None:
        logger.warning("Detected partial init state (pidx=%s but no signatory). "
                       "Clearing keeper for clean restart.", _pidx_raw)
        _clear_keeper(ks)
        setup_keeper(ks)

    cf = Configer(name=name, temp=True)  # Lambda only writes to /tmp

    try:
        _hby = Habery(name=name, temp=False, free=True, db=db, ks=ks, cf=cf,
                      salt=ks.salt, bran=ks.bran)
    except Exception as exc:
        if "Already incepted" in str(exc):
            logger.warning("Habery init hit 'Already incepted' (%s). "
                           "Clearing keeper and retrying.", exc)
            _clear_keeper(ks)
            setup_keeper(ks)
            _hby = Habery(name=name, temp=False, free=True, db=db, ks=ks, cf=cf,
                          salt=ks.salt, bran=ks.bran)
        else:
            raise

    # Get or create mailbox Hab — non-transferable, no witnesses (see note
    # above; keripy v2 forbids the non-trans + wits combination).
    _hab = _hby.habByName(alias)
    if _hab is None:
        def _make_mailbox_hab():
            return _hby.makeHab(
                name=alias, transferable=False,
                isith='1', icount=1, ncount=0, nsith='0',
            )
        try:
            _hab = _make_mailbox_hab()
        except Exception as exc:
            if "Already incepted" in str(exc):
                # Stale keeper state from a prior crashed init that wrote
                # key material but never completed the Hab record. Clear
                # keeper and rebuild Habery + retry.
                logger.warning("makeHab hit 'Already incepted' (%s). "
                               "Clearing keeper and rebuilding Habery.", exc)
                _clear_keeper(ks)
                setup_keeper(ks)
                _hby = Habery(name=name, temp=False, free=True,
                              db=db, ks=ks, cf=cf, salt=ks.salt, bran=ks.bran)
                _hab = _make_mailbox_hab()
            else:
                raise

    _hby.prefixes.add(_hab.pre)

    # No witness round-trip — see note about non-trans + wits at top of init().

    # Publish self-rpy (controller + mailbox roles)
    _publish_self_endpoints()

    # Register ForwardHandler so /fwd exn messages route to mbx.storeMsg
    from keri.app.forwarding import ForwardHandler
    _hby.exc.addHandler(ForwardHandler(hby=_hby, mbx=_hby.db))

    _parser = _hby.psr
    _initialized = True
    return _hby, _hab


def get_body_bytes(event):
    """Extract body bytes from API Gateway event."""
    body = event.get("body", "")
    if not body:
        return b""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    return bytes(body)


def _extract_cesr_stream(event):
    """Build a CESR ims byte stream from a Lambda HTTP event.

    Supports the keripy HTTP wire formats:
      - kli/streamCESRRequests: event Serder in body, attachments in
        the CESR-ATTACHMENT header.
      - Inline: full CESR stream (event + attachments) in body alone.

    Header lookup is case-insensitive (API Gateway header keys are
    case-sensitive in the event dict).
    """
    body = get_body_bytes(event)
    headers = event.get("headers") or {}
    attachment = ""
    for k, v in headers.items():
        if k.lower() == "cesr-attachment" and v:
            attachment = v
            break
    ims = bytearray(body)
    if attachment:
        ims.extend(_unwrap_attachment_group(attachment.encode("utf-8")))
    return ims


def _unwrap_attachment_group(attachment):
    """Strip a leading AttachmentGroup counter (-C or -V) from CESR-ATTACHMENT
    header bytes; pass through unchanged if no such wrapper is present.
    """
    if len(attachment) < 4:
        return attachment
    if attachment[:2] in (b'-C', b'-V'):
        try:
            from keri.core.counting import Counter
            Counter(qb64b=bytes(attachment[:4]))
        except Exception:
            return attachment
        return attachment[4:]
    return attachment


def _format_sse_events(hby, pre, topics):
    """Walk Mailboxer for each requested topic; format as SSE events.

    Args:
        hby: Habery (uses hby.db.cloneTopicIter)
        pre (str): recipient AID; topic keys in db.tpcs are pre+topic
        topics (dict): {topic_name: last_seen_ordinal}

    Returns:
        str: SSE-framed body. Empty string when no new messages on any topic.

    Topic key construction mirrors keri/app/forwarding.py:500 exactly:
        f"{recipient}/{topic}".encode("utf-8").
    """
    out = []
    pre_str = pre.decode("utf-8") if isinstance(pre, (bytes, bytearray)) else pre
    for name, last_on in topics.items():
        topic_key = f"{pre_str}/{name}".encode("utf-8")
        try:
            for on, _topic, msg in hby.db.cloneTopicIter(topic=topic_key,
                                                        fn=int(last_on) + 1):
                msg_text = bytes(msg).decode("utf-8")
                out.append(
                    f"id: {on}\nevent: {name}\nretry: 1000\ndata: {msg_text}\n\n"
                )
        except Exception as exc:
            logger.warning("cloneTopicIter failed for pre=%s topic=%s: %s",
                           pre, name, exc, exc_info=True)
    return "".join(out)


def _detect_mbx_query(ims):
    """Peek at the first message in ims; return its serder if it's a `qry`
    with r='/mbx' (or 'mbx' — accept both), else None.

    Returns None on parse error so the caller falls back to the default
    deposit path.
    """
    from keri.core import serdering
    try:
        serder = serdering.SerderKERI(raw=bytes(ims))
    except Exception:
        return None
    if serder.ked.get("t") == "qry" and serder.ked.get("r") in ("/mbx", "mbx"):
        return serder
    return None


async def _stream_mbx_response(pre, topics, soft_cap_s=780.0, poll_interval_s=1.0,
                               keepalive_interval_s=240.0):
    """Async generator yielding SSE-framed bytes for an mbx long-poll.

    Yields the initial drain immediately, then polls cloneTopicIter every
    poll_interval_s for new messages until soft_cap_s elapses. Emits a
    `:keepalive\\n\\n` comment frame every keepalive_interval_s of silence.

    Args:
        pre (str | bytes): recipient AID
        topics (dict): {topic_name: last_seen_ordinal}; copied internally so
            the caller's dict is not mutated
        soft_cap_s (float): max total streaming duration in seconds
        poll_interval_s (float): how often to poll for new messages
        keepalive_interval_s (float): how often to emit `:keepalive` when idle

    Yields:
        bytes: SSE-framed chunks (data frame or keepalive comment)
    """
    deadline = time.monotonic() + soft_cap_s
    last_event_ts = time.monotonic()
    pre_str = pre.decode("utf-8") if isinstance(pre, (bytes, bytearray)) else pre
    cursors = dict(topics)

    try:
        while time.monotonic() < deadline:
            produced = False
            for name, last_on in list(cursors.items()):
                topic_key = f"{pre_str}/{name}".encode("utf-8")
                try:
                    for on, _topic, msg in _hby.db.cloneTopicIter(topic=topic_key,
                                                                  fn=int(last_on) + 1):
                        msg_text = bytes(msg).decode("utf-8")
                        yield (f"id: {on}\nevent: {name}\nretry: 1000\n"
                               f"data: {msg_text}\n\n").encode("utf-8")
                        cursors[name] = on
                        produced = True
                except Exception as exc:
                    logger.warning("cloneTopicIter failed for pre=%s topic=%s: %s",
                                   pre, name, exc, exc_info=True)
            now = time.monotonic()
            if produced:
                last_event_ts = now
            elif now - last_event_ts >= keepalive_interval_s:
                yield b":keepalive\n\n"
                last_event_ts = now
            await asyncio.sleep(poll_interval_s)
    except asyncio.CancelledError:
        logger.debug("mbx stream cancelled for pre=%s (client disconnect)", pre_str)
        raise


class OOBIResource:
    """GET /oobi[/{aid}[/{role}[/{eid}]]] — serves OOBI rpy stream for the
    mailbox's own AID. Returns 404 for any other AID since the mailbox is
    authoritative only for itself.

    Body is plain ASCII CESR (qb64 is ASCII-safe), so Accept: */* clients
    receive raw bytes rather than base64.
    """

    async def on_get(self, req, resp, aid=None, role=None, eid=None):
        from keri.kering import Roles

        # Bare /oobi defaults to self-OOBI
        if aid is None:
            aid = _hab.pre

        # Mailbox is authoritative only for its own AID
        if aid != _hab.pre:
            resp.media = {"error": f"unknown aid: {aid}"}
            resp.status = falcon.HTTP_404
            return

        if aid not in _hby.kevers:
            resp.media = {"error": f"unknown aid: {aid}"}
            resp.status = falcon.HTTP_404
            return

        kever = _hby.kevers[aid]
        # Mirror the witness handler's false-404 guard: fullyWitnessed reads
        # receipt/wig counts off the GSI, which can lag a just-collected final
        # receipt, so retry the not-yet-witnessed path briefly before a false
        # 404. A truthy result returns at once. NOTE: _retry_negative sleeps
        # synchronously; the not-found retry therefore briefly blocks this
        # coroutine's event loop (bounded: (attempts-1)*delay ≈ 0.15s worst
        # case, only on the rare not-yet-witnessed path). The OOBI self-AID is
        # witnessed at cold-start init, so steady state hits the truthy fast path.
        if not _retry_negative(lambda: _hby.db.fullyWitnessed(kever.serder)):
            resp.media = {"error": "not fully witnessed"}
            resp.status = falcon.HTTP_404
            return

        eids = [eid] if eid else []
        msgs = _hab.replyToOobi(aid=aid, role=role, eids=eids)
        if not msgs and role is None:
            msgs = _hab.replyToOobi(aid=aid, role=Roles.mailbox, eids=eids)
            msgs.extend(_hab.replay(aid))

        if not msgs:
            resp.media = {"error": "no oobi content available"}
            resp.status = falcon.HTTP_404
            return

        resp.content_type = "application/cesr"
        resp.set_header("KERI-AID", aid)
        resp.data = bytes(msgs)
        resp.status = falcon.HTTP_200


class RootResource:
    """Handles all methods on /:
      - GET: mailbox status
      - POST/PUT: CESR ingest (deposit or buffered mbx-query)
    """

    async def on_get(self, req, resp):
        resp.media = {
            "mailbox": _hab.pre,
            "alias": _hab.name,
            "sn": _hab.kever.sn,
            "kevers": len(_hby.kevers),
        }
        resp.status = falcon.HTTP_200

    async def on_post(self, req, resp):
        await self._ingest(req, resp)

    async def on_put(self, req, resp):
        await self._ingest(req, resp)

    async def _ingest(self, req, resp):
        """POST / -- ingest CESR (/fwd exn deposit or qry r=/mbx poll).

        Two response paths:
          - Normal /fwd exn deposits: 204 (event routed to ForwardHandler via Exchanger).
          - qry r=/mbx: 200 + Content-Type: text/event-stream (buffered).

        (True streaming long-poll is added in Task 2.7.)
        """
        body = await req.bounded_stream.read()
        # Build a synthetic event dict so we can reuse _extract_cesr_stream
        # which expects Lambda-style {body, headers}.
        synthetic_event = {
            "body": body,
            "headers": {k: v for k, v in req.headers.items()},
        }
        ims = _extract_cesr_stream(synthetic_event)
        if not ims:
            resp.media = {"error": "empty body"}
            resp.status = falcon.HTTP_400
            return

        # Peek for mbx query before consuming ims via psr.parse.
        mbx_serder = _detect_mbx_query(ims)

        # framed=True: one HTTP POST = one message + counted attachments
        # (streamCESRRequests contract). Without it, -V/-C wrapped attachments
        # that claim more quadlets than present hang the parser.
        _hby.psr.parse(ims=ims, framed=True)
        _hby.kvy.processEscrows()

        if mbx_serder is not None:
            q = mbx_serder.ked.get("q") or {}
            pre = q.get("pre")
            topics = q.get("topics") or {}
            if not isinstance(pre, str) or not pre or not isinstance(topics, dict):
                resp.media = {"error": "qry/mbx requires q.pre (str) and q.topics (dict)"}
                resp.status = falcon.HTTP_400
                return
            resp.content_type = "text/event-stream"
            resp.set_header("Cache-Control", "no-cache")
            resp.set_header("X-Accel-Buffering", "no")
            resp.stream = _stream_mbx_response(pre, topics)
            resp.status = falcon.HTTP_200
            return

        resp.status = falcon.HTTP_204


def build_app():
    """Build the Falcon ASGI app. Calls init() so module globals _hby/_hab
    are populated before the first request lands.
    """
    init()
    app = falcon.asgi.App()
    app.add_route("/", RootResource())

    oobi = OOBIResource()
    app.add_route("/oobi", oobi)
    app.add_route("/oobi/{aid}", oobi)
    app.add_route("/oobi/{aid}/{role}", oobi)
    app.add_route("/oobi/{aid}/{role}/{eid}", oobi)

    return app


class _LazyApp:
    """Module-level ASGI application proxy for ``uvicorn mailbox_handler:app``.

    uvicorn (without ``--factory``) treats ``module:app`` as the ASGI app
    itself, so ``app`` must be an ASGI callable — not the ``build_app`` factory.
    Building eagerly at import would run ``init()`` (DynamoDB + keri) at module
    import, which breaks the host import smoke. This proxy defers ``build_app()``
    to the first ASGI call (the LWA cold start), then delegates every scope.
    """

    __slots__ = ("_app",)

    def __init__(self):
        self._app = None

    async def __call__(self, scope, receive, send):
        if self._app is None:
            self._app = build_app()
        return await self._app(scope, receive, send)


# Module-level ASGI app for uvicorn (run.sh: `uvicorn mailbox_handler:app`).
app = _LazyApp()
