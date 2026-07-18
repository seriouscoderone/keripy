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
