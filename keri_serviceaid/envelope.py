# -*- encoding: utf-8 -*-
"""Envelope provenance — turning a verified ACDC into the event shape folds read.

Every micro-app template is written against an event carrying `credential_said`,
`credential_issuer` and `credential_edges.<name>`. Those names are the loader's
RESERVED_ENVELOPE allowlist (concierge-api `loader/emission_bindings.py`), and until
this module existed nothing but a test ever populated them: the corpus described a
shape only the vector runner produced (register finding B11).

PURE ON PURPOSE — dict in, dict out, no `hby`, no keystore, no I/O. Two callers with
very different transports share it (`providers/admit.py` for an IPEX grant,
`providers/sealed_retrieval.py` for a watched anchor), and one implementation is the
whole point: two would be the drift the shared verification library exists to prevent.

IT REFUSES RATHER THAN DEGRADES, and that is register finding B22's fix. A CEL error
inside a map literal is *returned as a state value*, not raised — in BOTH fold
engines — and every append handler uses a map literal. So a partial envelope does not
fail where it was built; it poisons state and is misattributed one event later. This
module is the last place refusing is still cheap.
"""
from __future__ import annotations

#: The loader's allowlist, verbatim. Anything outside it is read by nothing.
ENVELOPE_KEYS = ("credential_said", "credential_issuer", "credential_edges")

#: ACDC reserved labels inside the `e` section that are not edges.
_E_RESERVED = frozenset({"d", "u", "o"})


class EnvelopeError(Exception):
    """No envelope could be produced. Never raised for an absent edge block —
    only for an unverified credential or a missing required field."""


def envelope_for(acdc: dict, *, verified: bool) -> dict:
    """Return the envelope for `acdc`, or raise `EnvelopeError`.

    `verified` is the caller's assertion that this ACDC's authenticity has already
    been established — the seal chain in `sealed_retrieval`, or IPEX admit's own
    parse. It is a required keyword rather than a default because a caller that has
    not verified must be forced to say so at the call site.
    """
    if not verified:
        raise EnvelopeError(
            "refusing to build an envelope for an ACDC that is not verified; "
            "a partial envelope becomes fold state instead of an error (B22)")
    if not isinstance(acdc, dict):
        raise EnvelopeError(f"ACDC must be a mapping, got {type(acdc).__name__}")
    for label in ("d", "i"):
        if not acdc.get(label):
            raise EnvelopeError(
                f"ACDC is missing required label {label!r}; no envelope is produced "
                "rather than one with an empty credential_said/credential_issuer")
    return {
        "credential_said": acdc["d"],
        "credential_issuer": acdc["i"],
        "credential_edges": _edges(acdc.get("e")),
    }


def _edges(e_section) -> dict:
    """`{edge_name: far node SAID}` for every edge in an expanded `e` block.

    Always a mapping, never absent: a fold reading `event.credential_edges` on a
    credential with no edges must get `{}`. An `e` in COMPACT form is a SAID string,
    which carries no edge names — that yields `{}` too, because the names genuinely
    are not present, and inventing them would be worse than reporting none.
    """
    if not isinstance(e_section, dict):
        return {}
    out = {}
    for name, edge in e_section.items():
        if name in _E_RESERVED or not isinstance(edge, dict):
            continue
        node = edge.get("n")
        if isinstance(node, str) and node:
            out[name] = node
    return out
