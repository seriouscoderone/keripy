"""Builds a minimal, VALID egf-doc/0.1 SAD for tests (two roles, one onboardable;
two authorities: US-UT bootstrap + US-CA production) and returns (said, sad)."""
from keri.core import coring


def fixture_egf() -> tuple[str, dict]:
    doc = {
        "d": "", "spec_version": "egf-doc/0.1", "version": "0.1.0",
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
            {"role_id": "regulator", "display_name": "UT DOI", "aid": "E" + "U" * 43, "phase": "bootstrap",
             "context": {"jurisdiction": "US-UT"}, "credentials": ["lic"], "endpoints": [],
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
    _, sad = coring.Saider.saidify(sad=doc, label="d")
    return sad["d"], sad
