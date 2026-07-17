"""Pure unit tests for request-access orchestration (Task 6, §A6).

`onboarding.py` is pure: no keystore/hby imports, no I/O beyond the injected
`resolver`. It walks an `EgfDocument` to derive a `RequestPlan` for a role's
onboarding "submit application" step, validates a caller-supplied payload
against the resolved micro-app command's payload schema, fills in
convenience defaults, and selects the authority the resulting grant is
addressed to.
"""
import pytest
from keri.core import coring

from keri_serviceaid.egf.documents import EgfDocument
from keri_serviceaid.egf.errors import EgfDocumentError, NoAuthorityError
from keri_serviceaid.egf.onboarding import (RequestPlan, build_attributes,
                                            derive_request, select_authority,
                                            validate_payload)
from keri_serviceaid.tests.egf.fixtures.make_fixture_egf import fixture_egf

MICRO_APP = {"d": "E" + "M" * 43,
             "commands": [{"id": "submit_application",
                           "payload_schema": {"type": "object", "additionalProperties": False,
                                              "properties": {"jurisdiction": {"type": "string"},
                                                             "submitted_at": {"type": "string", "format": "date-time"}},
                                              "required": ["jurisdiction", "submitted_at"]}}]}


class FakeResolver:
    def resolve_micro_app(self, said): assert said == "E" + "M" * 43; return MICRO_APP


def _doc():
    _, sad = fixture_egf(); return EgfDocument.from_sad(sad)


def test_derive_request_walks_egf_to_payload_schema():
    plan = derive_request(FakeResolver(), _doc(), "carrier")
    assert plan.command_id == "submit_application"
    assert plan.registry_name == plan.application_credential.schema_said
    assert set(plan.schema_saids_to_seed) == {"E" + "L" * 43, "E" + "P" * 43}
    assert "jurisdiction" in plan.payload_schema["properties"]


def test_validate_payload_rejects_bad_field():
    plan = derive_request(FakeResolver(), _doc(), "carrier")
    with pytest.raises(EgfDocumentError) as ei:
        validate_payload(plan, {"jurisdiction": 7, "submitted_at": "2026-07-16T00:00:00Z"})
    assert "jurisdiction" in str(ei.value)


def test_build_attributes_autofills_submitted_at():
    plan = derive_request(FakeResolver(), _doc(), "carrier")
    attrs = build_attributes(plan, {"jurisdiction": "US-UT"}, now_iso="2026-07-16T12:00:00+00:00")
    assert attrs["submitted_at"] == "2026-07-16T12:00:00+00:00"
    assert attrs["jurisdiction"] == "US-UT"


def test_select_authority_context_and_phase():
    doc, plan = _doc(), derive_request(FakeResolver(), _doc(), "carrier")
    a = select_authority(doc, plan, {"jurisdiction": "US-UT"}, ("bootstrap", "production"))
    assert a.display_name == "UT DOI"
    with pytest.raises(NoAuthorityError):
        select_authority(doc, plan, {"jurisdiction": "US-UT"}, ("production",))


def test_select_authority_ambiguous_match_raises():
    """TWO authorities matching the same (role, context, accepted phase) is as
    unusable to a caller as none: NoAuthorityError, listing offered contexts."""
    _, sad = fixture_egf()
    shadow = dict(sad["authorities"][0])
    shadow["display_name"] = "UT DOI (shadow)"
    shadow["aid"] = "E" + "V" * 43
    sad2 = dict(sad)
    sad2["authorities"] = list(sad["authorities"]) + [shadow]
    sad2["d"] = ""
    _, sad2 = coring.Saider.saidify(sad=sad2, label="d")
    doc = EgfDocument.from_sad(sad2)

    plan = derive_request(FakeResolver(), doc, "carrier")
    with pytest.raises(NoAuthorityError) as ei:
        select_authority(doc, plan, {"jurisdiction": "US-UT"}, ("bootstrap", "production"))
    msg = str(ei.value)
    assert "ambiguous" in msg
    assert "UT DOI" in msg and "UT DOI (shadow)" in msg
    assert "offered contexts" in msg and "US-UT" in msg
