"""The seal chain: a KEL seal commits to a TEL event, and the TEL event names the
credential. Skipping the middle step means trusting an unverified field of the seal.

The fixtures are REAL: keri.vdr.eventing.issue() saidifies the iss event (makify=True),
and the seal is derived from that event, so verifySealedBody genuinely re-derives and
the chain is actually exercised rather than short-circuited by a fake SAID."""
import pytest

from keri.core import coring
from keri.core.sealing import verifySealedBody
from keri.kering import Vrsn_1_0
from keri.vdr import eventing as teventing

from keri_serviceaid.providers.sealed_retrieval import (
    SealChainError, credential_said_from_seal,
)


def _said(*, seed: str) -> str:
    """A real, self-consistent qb64 SAID — for where a test needs a valid SAID string
    (a credential SAID, a registry SAID) but not a full credential."""
    _, sad = coring.Saider.saidify(sad={"d": "", "seed": seed})
    return sad["d"]


CRED_SAID = _said(seed="credential")   # the vcdig
REG_SAID = _said(seed="registry")      # the management-TEL SAID

#: A REAL iss event. issue(makify=True) computes its own `d`; for an iss event `i` IS
#: the vcdig (the credential SAID) and `d` is the event's own SAID.
#:
#: version=Vrsn_1_0 is REQUIRED here and is not the brief's assumption: issue()'s own
#: default (`version=Version`, i.e. KERI v2.0) has no `iss` ilk in the v2 Fields table
#: (serdering.py's per-protocol/per-version Fields map only carries iss/rev/bis/brv
#: under v1 -- TEL is v1-only upstream, matching the TRANSITIONAL note in
#: providers/issue.py). The real call site never hits this: Registry.issue() calls
#: `issueEvent(..., version=self.vcp.pvrsn)`, always pinning the registry's own
#: (currently v1) version explicitly. Confirmed against source before trusting it:
#: omitting version= raises `SerializeError: Invalid packet type (ilk) = iss for
#: protocol = KERI.` at serdering.py's _makify.
_ISS = teventing.issue(vcdig=CRED_SAID, regk=REG_SAID, version=Vrsn_1_0)
ISS = dict(_ISS.ked)

#: What `hab.interact(data=[SealEvent(iserder.pre, iserder.snh, iserder.said)])` lands in
#: the KEL: i = the credential SAID, s = the TEL sn, d = the TEL EVENT's SAID.
SEAL = {"i": ISS["i"], "s": ISS["s"], "d": _ISS.said}


def test_the_credential_said_comes_from_the_TEL_event_not_the_seal():
    """Both carry it, and only the TEL event's copy is committed to by seal['d'].
    Reading seal['i'] directly would trust a field nothing verified."""
    assert credential_said_from_seal(SEAL, ISS) == CRED_SAID


def test_a_TEL_event_that_does_not_re_derive_to_the_seal_is_refused():
    forged = dict(ISS, d=_said(seed="forged"))   # d no longer matches the event's bytes
    with pytest.raises(SealChainError, match="does not re-derive"):
        credential_said_from_seal(SEAL, forged)


def test_a_TEL_event_naming_a_DIFFERENT_credential_than_the_seal_is_refused():
    """The attack this closes: a real, correctly-SAID'd TEL event presented against a
    seal whose own `i` names a different credential. The event still re-derives (its `d`
    is untouched), so this reaches the credential-disagreement branch — which the
    fabricated-dict version never did."""
    seal_other = dict(SEAL, i=_said(seed="other-credential"))
    with pytest.raises(SealChainError, match="disagree"):
        credential_said_from_seal(seal_other, ISS)


def test_a_seal_with_no_d_is_refused_before_anything_is_fetched():
    with pytest.raises(SealChainError, match="carries no 'd'"):
        credential_said_from_seal({"i": CRED_SAID, "s": "0"}, ISS)


def test_a_TEL_event_with_no_i_is_refused():
    """A real d-bearing SAD that carries no credential SAID at all."""
    _, noi = coring.Saider.saidify(sad={"d": "", "t": "iss", "s": "0", "ri": REG_SAID})
    seal_noi = {"i": CRED_SAID, "s": "0", "d": noi["d"]}
    with pytest.raises(SealChainError, match="names no credential"):
        credential_said_from_seal(seal_noi, noi)


def test_verifySealedBody_alone_REJECTS_the_credential_and_that_is_why_this_module_exists():
    """Not a defect in verifySealedBody — a demonstration that it answers a different
    question. If this test ever starts passing `True`, the seal shape changed and this
    whole module's premise needs re-checking."""
    acdc = {"v": "ACDC10JSON0001ae_", "d": CRED_SAID, "i": "ECuoAid", "s": "ESchema"}
    assert verifySealedBody(SEAL, acdc) is False
