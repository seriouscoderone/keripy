# -*- encoding: utf-8 -*-
"""Verify that a body is the one a seal committed to.

The trust property behind log-triggered retrieval: authority comes from
re-deriving a retrieved body against a digest read from a KEL, not from
trusting whoever delivered it. Deliberately free of keripy state so an
application can call it without an Hab.

Two kinds of thing get sealed, and they are digested differently:

* A **SAD** -- any self-addressing data structure, which is every ACDC and
  every KERI message -- carries its own SAID in a `d` field, and that SAID is
  NOT a digest of the finished bytes. Each protocol derives it its own way, so
  the derivation is dispatched on the SAD's version string:

  - An **ACDC** (`v` beginning `ACDC`) is re-derived by `SerderACDC`. For
    protocol v2 that runs over the *most compact variant* -- nested `s`/`a`/
    `e`/`r` blocks replaced by their own SAIDs, then `v` resized to the compact
    length. `Saider.saidify` does none of that and gives a different answer for
    every real v2 ACDC.
  - A **KERI message** (`v` beginning `KERI`) is re-derived by `SerderKERI`.
  - An **unversioned** SAD has no protocol to consult, so `Saider.saidify` --
    dummy the `d` field, serialize, digest, write back -- is the derivation.

  The body's own `d` is checked against the seal before any of that, because
  `Saider._derive` overwrites `d` with a dummy before digesting and so never
  verifies the value that was supplied. Without the check a body could carry
  any `d` at all and still verify, and a caller that files content under
  `body["d"]` or follows an ACDC edge by it would be using an attacker's value.

* An **opaque blob** -- raw bytes with no `d`, such as a workbook or a
  manifest -- has no such structure, so it is digested as-is. `bytes`,
  `bytearray` and `memoryview` are all accepted, because keripy hands
  bytearrays around everywhere (`Hab.endorse`, `Parser`, and
  `ProdResponder.service()` all return one).

**A dict on the opaque path is a Python-only contract.** A dict with no `d` is
serialized here with exactly ``json.dumps(separators=(",", ":"),
ensure_ascii=False)`` and digested. That is a canonicalization, and it is
Python's: a producer that used ``ensure_ascii=True`` gets a different digest for
any non-ASCII value, and JavaScript's ``JSON.stringify`` emits ``1`` where
Python emits ``1.0``. Signify-TS is a first-class producer in this
architecture, so for anything that crosses a language boundary **bytes are the
only safe contract** -- serialize once at the producer, seal those bytes, and
pass those bytes. A `str` is refused outright rather than encoded, because
which encoding it should get is the caller's decision, not this function's.
"""
import json

from keri.core import coring, serdering
from keri.kering import Protocols, deversify

#: Protocol identifier -> the Serder subclass that knows how that protocol
#: derives a SAID. Dispatching here, rather than on the shape of the dict, is
#: what makes a real ACDC verify.
SERDERS = {Protocols.keri: serdering.SerderKERI,
           Protocols.acdc: serdering.SerderACDC}


def _saidOf(sad):
    """The SAID `sad` would have, derived the way its own protocol derives it.

    Raises rather than returning a sentinel, and every raise is a rejection:
    `deversify` raises on a malformed version string, the SERDERS lookup raises
    on a protocol we do not know how to re-derive, and the Serder raises on a
    field map its protocol cannot make. verifySealedBody turns all of them into
    False, so an unrecognized `v` fails closed.

    `verify=False` is deliberate. The question here is "what is this SAD's
    SAID", not "is this a valid protocol message" -- an invalid AID or a
    disallowed field is the consumer's business, and rejecting on it here would
    be a fresh source of false negatives on the honest path.
    """
    vs = sad.get("v")
    if isinstance(vs, str) and vs:
        return SERDERS[deversify(vs).proto](sad=dict(sad), makify=True,
                                            verify=False).said
    return coring.Saider.saidify(sad=dict(sad))[0].qb64


def verifySealedBody(seal, body):
    """True iff body re-derives to seal["d"]. False on mismatch or malformed input.

    A dict body carrying a `d` field is treated as a SAD: its `d` must equal the
    seal, and it must re-derive to it under its own protocol's rule. Anything
    else is digested as raw serialized bytes.
    """
    try:
        said = (seal or {}).get("d")
        if not said:
            return False
        if isinstance(body, dict) and "d" in body:
            if body["d"] != said:   # never checked by the derivation itself
                return False
            return _saidOf(body) == said
        if isinstance(body, str):   # no canonical byte form; see module docstring
            return False
        raw = (bytes(body) if isinstance(body, (bytes, bytearray, memoryview))
               else json.dumps(body, separators=(",", ":"),
                               ensure_ascii=False).encode())
        return coring.Diger(ser=raw).qb64 == said
    except Exception:
        # Round 1: non-dict seal, malformed/short said, unsupported derivation
        # code, unserializable body, JSON error.
        # SAD branch: malformed or unknown version string, a field map the
        # protocol's Serder refuses to make, a `d` whose code the Saider cannot
        # parse. All of them mean "cannot re-derive", which means not verified.
        return False
