import pytest
from keri_serviceaid.egf.documents import EgfDocument, _credential_from_sad
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

def test_authority_context_non_string_value_raises():
    _, sad = fixture_egf()
    sad = dict(sad)
    sad["authorities"] = [dict(sad["authorities"][0]), sad["authorities"][1]]
    sad["authorities"][0]["context"] = {"jurisdiction": {"nested": "x"}}
    with pytest.raises(EgfDocumentError):
        EgfDocument.from_sad(sad)

def test_authority_context_list_value_raises():
    _, sad = fixture_egf()
    sad = dict(sad)
    sad["authorities"] = [dict(sad["authorities"][0]), sad["authorities"][1]]
    sad["authorities"][0]["context"] = {"jurisdiction": ["US-UT"]}
    with pytest.raises(EgfDocumentError):
        EgfDocument.from_sad(sad)

def test_unknown_top_level_key_raises():
    _, sad = fixture_egf()
    sad = dict(sad)
    sad["unexpected_field"] = "surprise"
    with pytest.raises(EgfDocumentError):
        EgfDocument.from_sad(sad)

def test_bad_enum_openness_raises():
    _, sad = fixture_egf()
    sad = dict(sad)
    sad["ecosystem"] = dict(sad["ecosystem"])
    sad["ecosystem"]["openness"] = "semi"
    with pytest.raises(EgfDocumentError):
        EgfDocument.from_sad(sad)

def test_bad_said_pattern_authority_aid_raises():
    _, sad = fixture_egf()
    sad = dict(sad)
    sad["authorities"] = [dict(sad["authorities"][0]), sad["authorities"][1]]
    sad["authorities"][0]["aid"] = "not-a-said"
    with pytest.raises(EgfDocumentError):
        EgfDocument.from_sad(sad)

def test_not_found_lookups_raise():
    d = _doc()
    with pytest.raises(EgfDocumentError):
        d.role("nope")
    with pytest.raises(EgfDocumentError):
        d.credential("nope")
    with pytest.raises(EgfDocumentError):
        d.micro_app_for_role("nope")

def test_credential_entry_accepts_an_untargeted_credential():
    entry = _credential_from_sad({
        "id": "rating_attestation", "name": "Rating Attestation",
        "schema_said": "E" + "a" * 43, "issuer_role": "actuary",
        "disclosure_mode": "full", "chained_from": None, "self_issued": False})
    assert entry.holder_role is None

def test_self_issued_survives_and_stays_required():
    entry = _credential_from_sad({
        "id": "app", "name": "A", "schema_said": "E" + "a" * 43,
        "issuer_role": "carrier", "holder_role": "carrier",
        "disclosure_mode": "full", "chained_from": None, "self_issued": True})
    assert entry.self_issued is True and entry.holder_role == "carrier"
