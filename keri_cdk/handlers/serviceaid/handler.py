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

# Resolve libsodium BEFORE any keri import (keri imports are deferred into
# runtime.init()). On the zip+KeriRuntimeLayer entrypoint (handler.handler)
# there is no bootstrap wrapper, so the handler installs the find_library patch
# itself. ensure_libsodium() is idempotent and a no-op if the .so cannot be
# found (e.g. running under a host venv that loads libsodium normally).
# Best-effort: never let it break import of this module (host/test envs may
# import this as a package submodule where bare `bootstrap` is not on the path).
try:
    from bootstrap import ensure_libsodium  # flat /var/task (Lambda)
except ImportError:  # pragma: no cover - package-mode / host test envs
    try:
        from .bootstrap import ensure_libsodium  # package mode (tests)
    except ImportError:
        ensure_libsodium = None
if ensure_libsodium is not None:
    ensure_libsodium()

import base64
import json
import logging

# Dual-mode imports: relative when loaded as a package submodule (tests import
# keri_cdk.handlers.serviceaid.*); absolute when the asset dir is on sys.path as
# a flat /var/task (Lambda, handler="handler.handler"). Each serviceaid module
# uses this same idiom so the asset is self-contained without a parent package.
try:
    from . import runtime
    from .authorize import authorize
    from .contract import Request
    from .issuing import issue_grant
except ImportError:  # pragma: no cover - flat /var/task on Lambda
    import runtime
    from authorize import authorize
    from contract import Request
    from issuing import issue_grant

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _body_bytes(event) -> bytes:
    """Raises ValueError on an undecodable base64 body (mapped to 400)."""
    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        try:
            return base64.b64decode(body)
        except Exception as exc:  # binascii.Error (bad padding/alphabet) etc.
            raise ValueError("invalid base64 body") from exc
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
    # routing. The import is lazy so the HTTP path never touches it. The
    # inception module now lives in keri_cdk (Task 6 relocation); the asset that
    # ships THIS handler to Lambda must also ship _inception.py — see the
    # `handler_code_path` bundling note in keri_cdk/service_aid.py. Dual-mode:
    # absolute when _inception rides flat in /var/task, package-path otherwise.
    if "RequestType" in event:
        try:
            from _inception import on_event  # flat /var/task (Lambda)
        except ImportError:
            from keri_cdk._inception import on_event  # package mode (tests)
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

    try:
        ims = _body_bytes(event)
    except ValueError:
        return _json_response(400, {"error": "invalid base64 body"})
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
        logger.warning("no verified exn captured for %s — likely signature/KEL "
                       "verification failure", path)
        return _json_response(400, {"error": "no verified exn for route"})

    serder, attachments = captures[-1]   # newest capture wins

    # Bind the drained exn to THIS request: the Exchanger dispatches by the
    # exn's embedded `r` field, so a mismatched/stale capture must never be
    # processed as if it were the POSTed route.
    if serder.ked.get("r") != path:
        logger.warning("drained exn route %s != request path %s — rejecting "
                       "stale/mismatched capture", serder.ked.get("r"), path)
        return _json_response(400, {"error": "exn route does not match request path"})

    # Idempotency: a duplicate exn SAID short-circuits before dispatch.
    # "duplicate" must win the merge so clients can distinguish replays.
    cached = state.ledger.seen(serder.said)
    if cached is not None:
        return _json_response(200, {**cached, "status": "duplicate"})

    attrs = serder.ked.get("a", {}) or {}
    req = Request(sender=serder.ked["i"], payload=attrs,
                  credentials=[],   # v1: required-credential authz is DEFERRED —
                  # caller-attached ACDC extraction via Tevery is not yet wired, so
                  # Policy.required_schema must stay unset in v1 (it would deny all).
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
