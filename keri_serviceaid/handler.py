"""Service-AID Lambda entry point: CESR-ingest → 204 + CR fork.

Boundary B (server↔requesters): inbound is a CESR-over-HTTP envelope
(application/cesr body + CESR-ATTACHMENT header). We reassemble it into the
parser buffer (identical to TCP — the parser is transport-blind), parse, drain
the verified exn the Exchanger captured, and drive the pipeline. The HTTP layer
ALWAYS returns 204 No Content on an accepted ingest (zero KERI meaning); the only
real HTTP error is a malformed CESR envelope → 400. Every KERI-semantic outcome
is a signed message to the mailbox (the pipeline's job) or deliberate silence."""
from __future__ import annotations

# Resolve libsodium BEFORE any keri import.
try:
    from .bootstrap import ensure_libsodium
except ImportError:  # pragma: no cover
    ensure_libsodium = None
if ensure_libsodium is not None:
    ensure_libsodium()

import base64
import logging

from . import runtime
from . import pipeline

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _body_bytes(event) -> bytes:
    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    return body.encode("utf-8") if isinstance(body, str) else bytes(body)


def _reassemble_cesr(event) -> bytearray:
    """Rebuild the CESR stream from the application/cesr body + CESR-ATTACHMENT
    header (parseCesrHttpRequest-style). Raises ValueError on a malformed
    envelope (missing attachment header / undecodable body)."""
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if "cesr-attachment" not in headers:
        raise ValueError("missing CESR-ATTACHMENT header")
    body = _body_bytes(event)
    if not body:
        raise ValueError("empty body")
    ims = bytearray(body)
    ims.extend(headers["cesr-attachment"].encode("utf-8"))
    return ims


def handler(event, context):
    # CloudFormation Custom Resource (inception) shares this Lambda: events carry
    # RequestType instead of an HTTP method. Lazy import so the HTTP path is clean.
    if "RequestType" in event:
        try:
            from _inception import on_event           # flat /var/task on Lambda
        except ImportError:
            from keri_cdk._inception import on_event   # package mode (tests)
        return on_event(event, context)

    state = runtime.init()

    try:
        ims = _reassemble_cesr(event)
    except Exception as exc:
        logger.warning("malformed CESR envelope: %s", exc)
        return {"statusCode": 400}

    try:
        # _reassemble_cesr returns a fresh bytearray (never read again after this),
        # so no defensive copy is needed before the parser consumes it.
        state.hby.psr.parse(ims=ims, framed=True)
        state.hby.kvy.processEscrows()
        state.hby.exc.processEscrow()
    except Exception:
        logger.warning("CESR parse failed", exc_info=True)
        return {"statusCode": 400}

    # Drain every capture behavior and drive the pipeline for each verified exn.
    for behavior in list(state.hby.exc.routes.values()):
        if not hasattr(behavior, "drain"):
            continue
        for serder, attachments in behavior.drain():
            try:
                pipeline.process(state, serder, attachments)
            except Exception:
                # The pipeline already swallows per-outcome failures; this guards
                # the 204 contract against any unexpected provider error.
                logger.exception("pipeline error (suppressed — ingest still 204)")

    return {"statusCode": 204}
