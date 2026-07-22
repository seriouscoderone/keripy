"""Builds minimal, VALID egf-doc/0.1 SADs for tests. This module is the single
canonical owner of the "minimal valid egf-doc/0.1 shape" — every egf test
builds its fixture SAD here (or by copying-and-mutating one of these builders'
output), never by hand-assembling the shape inline, so the meta-schema's
required/enum surface only has to be kept in sync in one place.

`fixture_egf()`: two roles, one onboardable (form-mode); two authorities
(US-UT bootstrap + US-CA production). Returns (said, sad).

`apply_mode_egf(onboarding)`: one persona role ("actuary") whose `onboarding`
block is supplied by the caller — apply-mode / form-mode / half-form-mode
variants all flow through this one argument. Returns sad only (test_apply_mode.py
never needs the said standalone)."""
from keri.core import coring

ACTUARY_ROLE_SCHEMA_SAID = "E" + "A" * 43


def _saidify(doc: dict) -> dict:
    doc = dict(doc)
    doc["d"] = ""
    _, sad = coring.Saider.saidify(sad=doc, label="d")
    return sad


def fixture_egf() -> tuple[str, dict]:
    # Define endpoint for regulator authority
    regulator_aid = "E" + "U" * 43
    doc = {
        "d": "", "spec_version": "egf-doc/0.1", "version": "0.1.1",
        "ecosystem": {"id": "test-eco", "display_name": "Test", "openness": "closed", "description": "t"},
        "governance": {"phase": "bootstrap", "transition_plan": "t"},
        "roles": [
            {"id": "carrier", "display_name": "Carrier", "kind": "organization", "description": "c",
             "onboarding": {"grant_credential_id": "lic", "request_micro_app_said": "E" + "M" * 43,
                            "request_command_id": "submit_application"}},
            {"id": "regulator", "display_name": "Regulator", "kind": "government", "description": "r"},
        ],
        "credentials": [
            {"id": "lic", "name": "License", "schema_said": "E" + "L" * 43, "issuer_role": "regulator",
             "holder_role": "carrier", "disclosure_mode": "full", "chained_from": "app", "self_issued": False},
            {"id": "app", "name": "Application", "schema_said": "E" + "P" * 43, "issuer_role": "carrier",
             "holder_role": "carrier", "disclosure_mode": "full", "chained_from": None, "self_issued": True},
        ],
        "authorities": [
            {"role_id": "regulator", "display_name": "UT DOI", "aid": regulator_aid, "phase": "bootstrap",
             "context": {"jurisdiction": "US-UT"}, "credentials": ["lic"],
             "endpoints": [{"mode": "direct", "scheme": "tcp", "oobi_ref": regulator_aid}],
             "phase_note": "stand-in", "expected_transition": None},
            {"role_id": "regulator", "display_name": "CA DOI", "aid": "E" + "C" * 43, "phase": "production",
             "context": {"jurisdiction": "US-CA"}, "credentials": ["lic"], "endpoints": [],
             "phase_note": "", "expected_transition": None},
        ],
        "accepted_schema_saids": ["E" + "L" * 43, "E" + "P" * 43],
        "micro_apps": [{"said": "E" + "M" * 43, "id": "carrier-applies", "role_id": "carrier"}],
        "context_dimensions": [{"id": "jurisdiction", "applies_to_roles": ["regulator"], "kind": "enum",
                                "values_ref": "ISO3166-2:US", "prompt": "Which state?", "binds": "issuer_selection"}],
    }
    sad = _saidify(doc)
    return sad["d"], sad


def apply_mode_egf(onboarding: "dict | None") -> dict:
    """Minimal valid egf-doc/0.1 instance with one apply-mode-candidate
    persona role ("actuary", granted a role credential by "admin"). `onboarding`
    becomes that role's `onboarding` block verbatim, or is omitted entirely
    when None — test_apply_mode.py's apply-mode / form-mode / half-form-mode
    matrix is driven purely by varying this one argument."""
    admin_aid = "E" + "B" * 43
    role = {"id": "actuary", "display_name": "Actuarial", "kind": "individual",
            "description": "internal role"}
    if onboarding is not None:
        role["onboarding"] = onboarding
    doc = {
        "d": "", "spec_version": "egf-doc/0.1", "version": "0.1.0",
        "ecosystem": {"id": "t-internal", "display_name": "T", "openness": "closed",
                      "description": "test"},
        "governance": {"phase": "production", "transition_plan": "n/a"},
        "roles": [role,
                  {"id": "admin", "display_name": "Admin", "kind": "organization",
                   "description": "issuer"}],
        "credentials": [{"id": "actuary_role", "name": "Actuary Role",
                         "schema_said": ACTUARY_ROLE_SCHEMA_SAID, "issuer_role": "admin",
                         "holder_role": "actuary", "disclosure_mode": "full",
                         "chained_from": None, "self_issued": False}],
        "authorities": [{"role_id": "admin", "display_name": "Admin", "context": {},
                         "aid": admin_aid, "phase": "production",
                         "phase_note": "genuine internal authority",
                         "expected_transition": None,
                         "credentials": ["actuary_role"], "endpoints": []}],
        "accepted_schema_saids": [ACTUARY_ROLE_SCHEMA_SAID],
        "micro_apps": [],
        "context_dimensions": [],
    }
    return _saidify(doc)
