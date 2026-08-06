# -*- encoding: utf-8 -*-
"""The seal chain — from a KEL anchor to a credential you may believe.

Registry-backed issuance anchors `SealEvent(iserder.pre, iserder.snh, iserder.said)`
into the issuer's KEL (`providers/issue.py`). So the seal is
`{i: <credential SAID>, s: <TEL sn>, d: <TEL event SAID>}` — because an `iss` event's
`i` field IS `vcdig`, the credential's SAID (`vdr/eventing.py::issue`).

That makes `verifySealedBody(seal, acdc)` WRONG for this shape, and it is the trap this
module exists to close: that helper compares its body against `seal["d"]`, which here is
the TEL event, so handing it the retrieved credential returns False. It was written for
the registry-free shape where `d` was the credential, and it is correct for what it
claims.

The chain, and why it has three steps rather than one:

  (a) `pro` for `seal["d"]` -> the `iss` event; verify it with `verifySealedBody`.
  (b) read `i` from THAT event -> the credential SAID, now proven to be what the KEL
      committed to.
  (c) `pro` for the credential and check it re-derives to that SAID.

Reading `seal["i"]` directly and skipping (a) would work in the happy path and trust a
field nothing verified — a seal is `a`-block content and `hab.interact` accepts whatever
a controller puts there. The middle step is the requirement, not an optimisation.
"""
from __future__ import annotations

from keri.core.sealing import verifySealedBody


class SealChainError(Exception):
    """The chain from KEL anchor to credential could not be established."""


def credential_said_from_seal(seal: dict, iss_event: dict) -> str:
    """Return the credential SAID the KEL committed to, via `iss_event`.

    Raises `SealChainError` unless the event re-derives to the seal AND names a
    credential consistent with the seal's own `i`.
    """
    said = (seal or {}).get("d")
    if not said:
        raise SealChainError(
            "seal carries no 'd'; only digest seals name a TEL event, and "
            "SealLast/SealBack/SealRoot are legal KEL content that is not this")
    if not verifySealedBody(seal, iss_event):
        raise SealChainError(
            f"the TEL event does not re-derive to the seal's d {said!r}; the KEL "
            "committed to different bytes than the ones supplied")
    credential = (iss_event or {}).get("i")
    if not credential:
        raise SealChainError(
            "the TEL event names no credential in 'i'; an iss event's i IS the "
            "credential SAID, so an event without one cannot start a chain")
    stated = (seal or {}).get("i")
    if stated and stated != credential:
        raise SealChainError(
            f"seal and TEL event disagree about the credential: seal says "
            f"{stated!r}, the committed event says {credential!r}")
    return credential


def envelope_from_anchor(seal: dict, iss_event: dict, acdc: dict) -> dict:
    """The watch path's whole verification, ending in an envelope.

    Step (c) of the chain: the credential must BE the one the TEL event named. Only
    then is `verified=True` an honest claim, which is why `envelope_for` takes it as a
    required keyword rather than assuming it.
    """
    from keri_serviceaid.envelope import envelope_for

    proven = credential_said_from_seal(seal, iss_event)
    if (acdc or {}).get("d") != proven:
        raise SealChainError(
            f"the supplied ACDC {(acdc or {}).get('d')!r} is not the credential the "
            f"chain proved ({proven!r}); a well-formed credential that the KEL never "
            "committed to is exactly the substitution this chain exists to catch")
    return envelope_for(acdc, verified=True)
