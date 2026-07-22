# -*- encoding: utf-8 -*-
"""Apply-mode onboarding: an EGF role may onboard via a bare IPEX apply
(grant_credential_id only) instead of the form-driven micro-app request."""
import pytest
from keri.core import coring

from keri_serviceaid.egf.documents import EgfDocument, Onboarding
from keri_serviceaid.egf.errors import EgfDocumentError
from keri_serviceaid.egf.onboarding import ApplyPlan, derive_apply_request, derive_request

SCHEMA_A = "E" + "A" * 43
ADMIN_AID = "E" + "B" * 43


def _saidified(sad: dict) -> dict:
    sad = dict(sad)
    sad["d"] = ""
    _, out = coring.Saider.saidify(sad=sad, label="d")
    return out


def _egf_sad(onboarding: dict | None) -> dict:
    """Minimal valid egf-doc/0.1 instance with one apply-mode-candidate role.
    Field set mirrors docs/insurance/egf's instance; extend if the meta-schema
    names a missing required key."""
    role = {"id": "actuary", "display_name": "Actuarial", "kind": "individual",
            "description": "internal role"}
    if onboarding is not None:
        role["onboarding"] = onboarding
    return _saidified({
        "d": "",
        "spec_version": "egf-doc/0.1",
        "version": "0.1.0",
        "ecosystem": {"id": "t-internal", "display_name": "T", "openness": "closed",
                      "description": "test"},
        "governance": {"phase": "production", "transition_plan": "n/a"},
        "roles": [role,
                  {"id": "admin", "display_name": "Admin", "kind": "organization",
                   "description": "issuer"}],
        "credentials": [{"id": "actuary_role", "name": "Actuary Role",
                         "schema_said": SCHEMA_A, "issuer_role": "admin",
                         "holder_role": "actuary", "disclosure_mode": "full",
                         "chained_from": None, "self_issued": False}],
        "authorities": [{"role_id": "admin", "display_name": "Admin", "context": {},
                         "aid": ADMIN_AID, "phase": "production",
                         "phase_note": "genuine internal authority",
                         "expected_transition": None,
                         "credentials": ["actuary_role"], "endpoints": []}],
        "accepted_schema_saids": [SCHEMA_A],
        "micro_apps": [],
        "context_dimensions": [],
    })


def test_apply_mode_onboarding_parses_and_role_is_a_persona():
    egf = EgfDocument.from_sad(_egf_sad({"grant_credential_id": "actuary_role"}))
    role = egf.role("actuary")
    assert role.onboarding is not None
    assert role.onboarding.apply_mode is True
    assert role.onboarding.request_micro_app_said == ""
    assert [r.id for r in egf.personas()] == ["actuary"]


def test_form_mode_onboarding_still_parses_with_apply_mode_false():
    egf = EgfDocument.from_sad(_egf_sad({
        "grant_credential_id": "actuary_role",
        "request_micro_app_said": "E" + "C" * 43,
        "request_command_id": "submit_application"}))
    assert egf.role("actuary").onboarding.apply_mode is False


def test_meta_schema_rejects_half_form_mode():
    # request_micro_app_said without request_command_id violates dependentRequired
    with pytest.raises(EgfDocumentError):
        EgfDocument.from_sad(_egf_sad({
            "grant_credential_id": "actuary_role",
            "request_micro_app_said": "E" + "C" * 43}))


def test_derive_apply_request_returns_plan():
    egf = EgfDocument.from_sad(_egf_sad({"grant_credential_id": "actuary_role"}))
    plan = derive_apply_request(egf, "actuary")
    assert isinstance(plan, ApplyPlan)
    assert plan.role_id == "actuary"
    assert plan.grant_credential.schema_said == SCHEMA_A
    assert plan.schema_saids_to_seed == (SCHEMA_A,)


def test_derive_apply_request_rejects_role_without_onboarding():
    egf = EgfDocument.from_sad(_egf_sad({"grant_credential_id": "actuary_role"}))
    with pytest.raises(EgfDocumentError):
        derive_apply_request(egf, "admin")


def test_derive_request_rejects_apply_mode_role():
    egf = EgfDocument.from_sad(_egf_sad({"grant_credential_id": "actuary_role"}))
    with pytest.raises(EgfDocumentError):
        derive_request(resolver=None, egf=egf, role_id="actuary")
