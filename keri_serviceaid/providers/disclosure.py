# -*- encoding: utf-8 -*-
"""What a controller consents to disclose when a peer prods for it.

`ProdResponder` takes a `disclosable` map of `said -> SAD`. Nothing in `src/`
ever built one, so a wired responder still answered nothing: `respond()` returns
None for any SAID it does not find there (`prodding.py`).

The map here is computed from a RULE over the controller's own credentials,
never hand-curated. That distinction is the whole design. A hand-maintained
`disclosable` dict is an access-control list keyed by SAID instead of by AID,
and the constitution's Principle VIII (NON-NEGOTIABLE) forbids exactly that:
"Authority MUST derive from credentials and KERI protocol guarantees -- never
from app-layer permissioning (no plugin sandboxes, no ACL tables, no
org-managed accounts)."

THE RULE. An ACDC is openly disclosable only if it is BOTH:

  * **untargeted** -- no Issuee, i.e. no `a.i`. ACDC v1.1 §"the issuance is
    effectively addressed 'to whom it may concern'". A TARGETED credential
    names the party it was issued to; handing it to whoever asks would leak
    someone's role credential, and `openPolicy` alone cannot tell the
    difference (measured: it returns True for a targeted role ACDC issued by
    the same hab).
  * **public** -- no top-level `u` (UUID salty nonce). ACDC v1.1: "an ACDC
    without a top-level UUID, `u`, field SHOULD be considered a public
    (non-confidential) ACDC." Without `u` the seal over it is "merely a
    verifiable binding" (KERI v1.1 §Routing Security), so the data is
    discoverable by anyone who can read the KEL and guess the schema. Refusing
    to hand over what a rainbow table already yields protects nothing; but a
    credential that DID carry `u` was deliberately blinded, and disclosing it
    on request would defeat the blinding.

Both conditions are properties of the artifact itself, so the answer to "may
this be disclosed" is derived, reproducible, and needs no roster to keep in
sync with reality.

WHAT THIS RULE DOES NOT DO. It is not a confidentiality model. It cannot
express "this ecosystem's actuaries may see mandates but competitors may
not" -- for that, the disclosee must present authority and the responder must
verify it, which is what `ProdResponder`'s `policy` hook and the prod's
`q["az"]` field exist for. This rule is the floor: publish what is already
public, withhold everything else.
"""
from __future__ import annotations

from keri import help

logger = help.ogler.getLogger(__name__)


def is_openly_disclosable(sad) -> bool:
    """True when `sad` is an untargeted, public ACDC. See module docstring."""
    if not isinstance(sad, dict):
        return False
    if sad.get("u"):                       # deliberately blinded -- not ours to reveal
        return False
    attrs = sad.get("a")
    if isinstance(attrs, dict) and attrs.get("i"):
        return False                       # targeted: addressed to a specific party
    return True


def disclosable_bodies(reger) -> dict:
    """`said -> SAD` for every credential in `reger` the rule permits disclosing.

    Reads the registry the controller already has; never mutates it. Returns a
    plain dict so `ProdResponder` can be constructed from it directly, and so a
    caller can log or diff exactly what it is prepared to hand out.
    """
    out = {}
    try:
        items = list(reger.creds.getTopItemIter(keys=()))
    except Exception:                      # noqa: BLE001 -- an unreadable reger discloses nothing
        logger.exception("disclosure.registry_unreadable")
        return out

    for keys, serder in items:
        said = keys[0] if isinstance(keys, (tuple, list)) else keys
        sad = getattr(serder, "sad", None)
        if sad is None or not said:
            continue
        if is_openly_disclosable(sad):
            out[said] = sad
        else:
            logger.debug("disclosure.withheld said=%s", str(said)[:12])
    return out
