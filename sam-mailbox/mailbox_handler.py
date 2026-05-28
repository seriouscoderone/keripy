"""KERI Mailbox Lambda handler — Falcon ASGI app served via uvicorn behind
AWS Lambda Web Adapter for response-streaming-compatible SSE long-poll.

Cold-start populates module-level singletons _hby, _hab via init(); subsequent
warm invocations reuse them. build_app() wires Falcon routes to Resource
classes and returns the ASGI App that bootstrap.py boots with uvicorn.
"""

import base64
import json
import logging
import os

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


class StatusResource:
    """GET / — return mailbox status and identifier."""

    async def on_get(self, req, resp):
        resp.media = {
            "mailbox": _hab.pre,
            "alias": _hab.name,
            "sn": _hab.kever.sn,
            "kevers": len(_hby.kevers),
        }
        resp.status = falcon.HTTP_200


def build_app():
    """Build the Falcon ASGI app with all routes wired.

    Called by bootstrap.py at uvicorn startup. Does NOT call init() — that
    is deferred until the first request hits a route that needs the Habery
    (LWA's readiness probe path /status, configured in template.yaml, hits
    StatusResource which DOES need _hab populated; for now this is left as
    a known issue resolved in Task 2.8 when init() lands).
    """
    app = falcon.asgi.App()
    app.add_route("/", StatusResource())
    return app
