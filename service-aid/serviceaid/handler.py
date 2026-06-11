"""Generic Service AID Lambda entry point: verify → authorize → dispatch → reply.

The inbound body is a self-contained CESR stream: the caller's KEL followed
by a signed exn for one of the registered routes. Verification happens
entirely inside keripy (Parser → Kevery for the KEL, Exchanger for the exn
signatures against the just-ingested key state); the developer function only
ever sees a verified, authorized `Request`.

Note: keripy's Parser swallows most validation errors (logs and keeps
parsing — see keri/core/parsing.py allParsator's non-extraction handler), so
a bad signature usually surfaces here as an EMPTY capture drain rather than
an exception. Both paths return 400.
"""
from __future__ import annotations

import base64
import json
import logging

from . import runtime
from .authorize import authorize
from .contract import Request
from .issuing import issue_grant

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _body_bytes(event) -> bytes:
    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode("utf-8") if isinstance(body, str) else bytes(body)


def _cesr_response(status, body):
    if body is None:
        return {"statusCode": status}
    return {"statusCode": status,
            "headers": {"Content-Type": "application/cesr"},
            "body": bytes(body).decode("utf-8")}  # CESR text domain is ASCII


def _json_response(status, obj):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(obj)}


def handler(event, context):
    # CloudFormation Custom Resource events (inception) share this Lambda.
    # They carry RequestType instead of httpMethod — delegate before HTTP
    # routing. serviceaid.cdk lands in Task 11; the import is lazy so the
    # HTTP path never touches it.
    if "RequestType" in event:
        from .cdk.inception import on_event
        return on_event(event, context)

    state = runtime.init()
    method = event.get("httpMethod", "GET")
    path = (event.get("path", "/") or "/").rstrip("/") or "/"

    if method == "GET" and path == "/":
        return _json_response(200, {"service": state.hab.pre,
                                    "alias": state.cfg.alias,
                                    "routes": state.svc.routes})

    cmd = state.svc.lookup(path)
    if cmd is None:
        return _json_response(404, {"error": f"no command for route {path}"})

    ims = _body_bytes(event)
    if not ims:
        return _json_response(400, {"error": "empty body"})

    behavior = state.hby.exc.routes.get(path)
    if behavior is None:  # runtime.init registers a capture handler per route
        logger.error("route %s registered but has no Exchanger behavior", path)
        return _json_response(500, {"error": "route misconfigured"})

    try:
        # framed=True: each message is one frame of msg + counted attachments
        # (see sam-witness/witness_handler.py:326-332 — an unframed -V
        # attachment group can stall the parser generator until the API
        # Gateway timeout). parse() still loops over ALL frames in ims, so
        # the KEL + exn multi-message body parses in one call.
        state.hby.psr.parse(ims=bytearray(ims), framed=True)
        state.hby.kvy.processEscrows()
        state.hby.exc.processEscrow()
    except Exception as exc:  # verification failure => cannot sign a KERI reply
        logger.warning("verification failed on %s: %s", path, exc, exc_info=True)
        return _json_response(400, {"error": "verification failed"})

    captures = behavior.drain()          # sole read path (cross-request safety)
    if not captures:
        return _json_response(400, {"error": "no verified exn for route"})

    serder, attachments = captures[-1]   # newest capture wins

    # Idempotency: a duplicate exn SAID short-circuits before dispatch.
    cached = state.ledger.seen(serder.said)
    if cached is not None:
        return _json_response(200, {"status": "duplicate", **cached})

    attrs = serder.ked.get("a", {}) or {}
    req = Request(sender=serder.ked["i"], payload=attrs, credentials=[],
                  message_said=serder.said,
                  payload_said=attrs.get("d", "") if isinstance(attrs, dict) else "",
                  route=path)

    ok, reason = authorize(req, state.policy)
    if not ok:
        logger.info("authorization denied on %s: %s", path, reason)
        return _json_response(403, {"error": "forbidden", "reason": reason})

    try:
        reply = cmd.fn(req)
    except Exception as exc:           # handler raised => retry-safe, not recorded
        logger.error("handler raised on %s: %s", path, exc, exc_info=True)
        return _json_response(500, {"error": "handler error"})

    if reply.kind == "none":
        state.ledger.record(serder.said, {"status": "ok"})
        return _cesr_response(204, None)
    if reply.kind == "reject":
        return _json_response(403, {"error": "rejected", "reason": reply.reason})

    grant = issue_grant(state.hby, state.hab, state.rgy,
                        schema_said=cmd.issues, recipient=reply.recipient,
                        attributes=reply.attributes, edges=reply.edges,
                        rules=reply.rules, registry_name=state.cfg.alias)
    state.ledger.record(serder.said, {"status": "ok"})   # BEFORE returning the reply
    return _cesr_response(200, grant)
