# -*- encoding: utf-8 -*-
"""Verify that a body is the one a seal committed to.

The trust property behind log-triggered retrieval: authority comes from
re-deriving a retrieved body against a digest read from a KEL, not from
trusting whoever delivered it. Deliberately free of keripy state so an
application can call it without an Hab.
"""
import json

from keri.core import coring


def verifySealedBody(seal, body):
    """True iff body re-derives to seal["d"]. False on any mismatch or malformed input."""
    said = (seal or {}).get("d")
    if not said:
        return False
    try:
        raw = body if isinstance(body, bytes) else json.dumps(
            body, separators=(",", ":"), ensure_ascii=False).encode()
        return coring.Diger(ser=raw).qb64 == said
    except Exception:          # malformed said, unsupported code, json serialization error
        return False
