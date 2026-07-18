import pytest
from keri_serviceaid.egf.documents import EgfDocument, Endpoint
from keri_serviceaid.egf.errors import EgfDocumentError
from keri_serviceaid.tests.egf.fixtures.make_fixture_egf import fixture_egf


def _doc():
    _, sad = fixture_egf()
    return EgfDocument.from_sad(sad), sad


def test_authority_endpoints_are_typed():
    doc, _ = _doc()
    (auth,) = doc.authorities("regulator", accept_phases=("bootstrap",))
    (ep,) = auth.endpoints
    assert isinstance(ep, Endpoint)
    assert ep.mode == "direct" and ep.scheme == "tcp"
    assert ep.oobi_ref == auth.aid


def test_empty_endpoints_still_valid():
    doc, sad = _doc()
    import copy
    from keri.core import coring
    probe = copy.deepcopy(sad)
    probe["authorities"][0]["endpoints"] = []
    probe["d"] = ""
    _, resaid = coring.Saider.saidify(sad=probe, label="d")
    doc2 = EgfDocument.from_sad(resaid)
    (auth,) = doc2.authorities("regulator", accept_phases=("bootstrap",))
    assert auth.endpoints == ()


def test_endpoint_missing_mode_fails_closed():
    _, sad = _doc()
    import copy
    from keri.core import coring
    probe = copy.deepcopy(sad)
    probe["authorities"][0]["endpoints"] = [{"scheme": "tcp"}]  # no mode
    probe["d"] = ""
    _, resaid = coring.Saider.saidify(sad=probe, label="d")
    with pytest.raises(EgfDocumentError):
        EgfDocument.from_sad(resaid)


def test_instance_version_is_informative_not_pinned():
    """The SAID is the normative version; properties.version must accept
    any semver string (the live insurance EGF still says 0.1.0)."""
    _, sad = _doc()
    import copy
    from keri.core import coring
    probe = copy.deepcopy(sad)
    probe["version"] = "0.1.0"
    probe["d"] = ""
    _, resaid = coring.Saider.saidify(sad=probe, label="d")
    EgfDocument.from_sad(resaid)  # must not raise
