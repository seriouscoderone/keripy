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
import time

import falcon
import falcon.asgi

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Module-level singletons (warm across Lambda invocations)
_hby = None
_hab = None
_parser = None
_initialized = False


def init():
    """Cold-start: set up Habery with DynamoDB backends, mailbox Hab, etc.

    Implemented incrementally in later tasks (Task 2.8).
    """
    raise NotImplementedError("init() implemented in Task 2.8")


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
        if not _hby.db.fullyWitnessed(kever.serder):
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
    """Build the Falcon ASGI app with all routes wired.

    Called by bootstrap.py at uvicorn startup. Does NOT call init() — that
    is deferred until the first request hits a route that needs the Habery
    (LWA's readiness probe path /status, configured in template.yaml, hits
    RootResource which DOES need _hab populated; for now this is left as
    a known issue resolved in Task 2.8 when init() lands).
    """
    app = falcon.asgi.App()
    app.add_route("/", RootResource())

    oobi = OOBIResource()
    app.add_route("/oobi", oobi)
    app.add_route("/oobi/{aid}", oobi)
    app.add_route("/oobi/{aid}/{role}", oobi)
    app.add_route("/oobi/{aid}/{role}/{eid}", oobi)

    return app
