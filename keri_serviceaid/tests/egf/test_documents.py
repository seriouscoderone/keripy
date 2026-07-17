import pytest
from keri_serviceaid.egf.documents import EgfDocument
from keri_serviceaid.egf.errors import EgfDocumentError
from keri_serviceaid.tests.egf.fixtures.make_fixture_egf import fixture_egf


def _doc():
    _, sad = fixture_egf()
    return EgfDocument.from_sad(sad)

def test_personas_lists_only_onboardable_roles():
    assert [r.id for r in _doc().personas()] == ["carrier"]

def test_role_and_credential_lookup():
    d = _doc()
    assert d.role("carrier").onboarding.request_command_id == "submit_application"
    assert d.credential("lic").chained_from == "app"
    assert d.credential("app").self_issued is True

def test_authorities_default_production_only():
    auths = _doc().authorities("regulator")
    assert [a.aid for a in auths] == ["E" + "C" * 43]          # bootstrap excluded by default

def test_authorities_bootstrap_opt_in_and_context_filter():
    d = _doc()
    got = d.authorities("regulator", context={"jurisdiction": "US-UT"},
                        accept_phases=("bootstrap", "production"))
    assert [a.display_name for a in got] == ["UT DOI"]
    assert d.authorities("regulator", context={"jurisdiction": "US-TX"},
                         accept_phases=("bootstrap", "production")) == []

def test_meta_schema_violation_raises_document_error():
    _, sad = fixture_egf()
    sad = dict(sad); sad.pop("authorities")
    with pytest.raises(EgfDocumentError):
        EgfDocument.from_sad(sad)

def test_unsupported_spec_version_raises():
    _, sad = fixture_egf()
    sad = dict(sad); sad["spec_version"] = "egf-doc/9.9"
    with pytest.raises(EgfDocumentError):
        EgfDocument.from_sad(sad)
