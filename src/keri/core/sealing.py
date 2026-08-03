# -*- encoding: utf-8 -*-
"""Verify that a body is the one a seal committed to.

The trust property behind log-triggered retrieval: authority comes from
re-deriving a retrieved body against a digest read from a KEL, not from
trusting whoever delivered it. Deliberately free of keripy state so an
application can call it without an Hab.

Two kinds of thing get sealed, and they are digested differently:

* A **SAD** -- any self-addressing data structure, which is every ACDC and
  every attestation -- carries its own SAID in a `d` field. Its SAID is
  computed by Saider.saidify: a correctly-sized dummy goes in `d`, the result
  is serialized and digested, and the digest is written back into `d`. A plain
  digest over the finished bytes gives a DIFFERENT value and always fails.
* An **opaque blob** -- raw bytes with no `d`, such as a workbook or a
  manifest -- has no such structure, so it is digested as-is.

Dispatching on the presence of `d` is what lets one predicate serve both.
"""
import json

from keri.core import coring


def verifySealedBody(seal, body):
    """True iff body re-derives to seal["d"]. False on mismatch or malformed input.

    A dict body carrying a `d` field is treated as a SAD and re-derived with
    Saider.saidify; anything else is digested as raw serialized bytes.
    """
    try:
        said = (seal or {}).get("d")
        if not said:
            return False
        if isinstance(body, dict) and "d" in body:
            rederived, _ = coring.Saider.saidify(sad=dict(body))
            return rederived.qb64 == said
        raw = body if isinstance(body, bytes) else json.dumps(
            body, separators=(",", ":"), ensure_ascii=False).encode()
        return coring.Diger(ser=raw).qb64 == said
    except Exception:          # non-dict seal, malformed said, unsupported code, json error
        return False
