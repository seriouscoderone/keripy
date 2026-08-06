# -*- encoding: utf-8 -*-
"""The R3 walk: an attestation's schema -> the role the ecosystem says must issue it."""
import pytest

from keri_serviceaid.egf.documents import EgfDocument


def _egf_sad():
    """A minimal but STRUCTURALLY REAL egf-doc/0.1 — it goes through from_sad, so the
    meta-schema validates it exactly like the live document. Not a flat stand-in."""
    return {
        "d": "E" + "A" * 43,
        "spec_version": "egf-doc/0.1",
        "version": "0.2.0",
        "ecosystem": {"id": "t", "display_name": "T", "openness": "closed",
                      "description": "test"},
        "governance": {"phase": "production", "transition_plan": "none"},
        "roles": [
            {"id": "cuo", "display_name": "CUO", "kind": "individual",
             "description": "declares mandates"},
            {"id": "actuary", "display_name": "Actuary", "kind": "individual",
             "description": "attests rate programs"},
        ],
        "credentials": [
            {"id": "cuo_role", "name": "CUO Role", "schema_said": "E" + "C" * 43,
             "issuer_role": "admin", "holder_role": "cuo", "disclosure_mode": "full",
             "chained_from": None, "self_issued": False},
            {"id": "actuary_role", "name": "Actuary Role", "schema_said": "E" + "R" * 43,
             "issuer_role": "admin", "holder_role": "actuary", "disclosure_mode": "full",
             "chained_from": None, "self_issued": False},
        ],
        "authorities": [
            {"role_id": "admin", "display_name": "Admin", "context": {},
             "aid": "E" + "D" * 43, "phase": "production", "phase_note": "",
             "expected_transition": None,
             "credentials": ["cuo_role", "actuary_role"], "endpoints": []},
        ],
        "accepted_schema_saids": ["E" + "M" * 43, "E" + "P" * 43],
        "micro_apps": [
            {"said": "E" + "1" * 43, "id": "cuo-declares", "role_id": "cuo"},
            {"said": "E" + "2" * 43, "id": "actuary-attests", "role_id": "actuary"},
        ],
        "context_dimensions": [],
    }


class _Resolver:
    """Stands in for EgfResolver.resolve_micro_app ONLY — same call signature, same
    return type (a parsed template dict keyed by SAID)."""

    def __init__(self, templates):
        self._templates = templates

    def resolve_micro_app(self, said):
        return self._templates[said]


def _templates():
    """Templates in the REAL corpus shape: exports nest the schema under `.schema`,
    imports are flat. Note the mandate schema E-M is EXPORTED by cuo and IMPORTED by
    the actuary — a walk that matched imports[] would find two producers for it and
    still look correct on the actuary case."""
    return {
        "E" + "1" * 43: {
            "role": {"id": "cuo"},
            "credentials": {
                "exports": [{"id": "product_mandate",
                             "schema": {"schema_said": "E" + "M" * 43}}],
                "imports": [{"id": "cuo_role", "expected_issuer_role": "admin",
                             "expected_schema_said": "E" + "C" * 43}],
            },
        },
        "E" + "2" * 43: {
            "role": {"id": "actuary"},
            "credentials": {
                "exports": [{"id": "rate_program_attestation",
                             "schema": {"schema_said": "E" + "P" * 43}}],
                "imports": [{"id": "actuary_role", "expected_issuer_role": "admin",
                             "expected_schema_said": "E" + "R" * 43},
                            {"id": "product_mandate", "expected_issuer_role": "cuo",
                             "expected_schema_said": "E" + "M" * 43}],
            },
        },
    }


def test_micro_apps_accessor_exposes_every_ref():
    egf = EgfDocument.from_sad(_egf_sad())
    assert [m.role_id for m in egf.micro_apps()] == ["cuo", "actuary"]
    assert [m.said for m in egf.micro_apps()] == ["E" + "1" * 43, "E" + "2" * 43]


def test_credentials_for_holder_selects_by_holder_role():
    egf = EgfDocument.from_sad(_egf_sad())
    assert [c.id for c in egf.credentials_for_holder("actuary")] == ["actuary_role"]
    assert egf.credentials_for_holder("nobody") == []


def test_the_walk_resolves_an_attestation_schema_to_its_issuing_role():
    from keri_serviceaid.egf.issuer_role import issuer_role_import_for_schema

    egf = EgfDocument.from_sad(_egf_sad())
    imp = issuer_role_import_for_schema(egf, _Resolver(_templates()), "E" + "P" * 43)
    assert imp == {"expected_schema_said": "E" + "R" * 43,
                   "expected_issuer_role": "admin"}


def test_it_keys_on_exports_not_imports():
    """The mandate schema is exported by ONE template and imported by TWO. Keying on
    imports would find two producers and raise; keying on exports resolves cleanly."""
    from keri_serviceaid.egf.issuer_role import issuer_role_import_for_schema

    egf = EgfDocument.from_sad(_egf_sad())
    imp = issuer_role_import_for_schema(egf, _Resolver(_templates()), "E" + "M" * 43)
    assert imp["expected_schema_said"] == "E" + "C" * 43      # cuo_role


def test_the_resolved_import_is_the_role_credential_not_the_attestation_schema():
    from keri_serviceaid.egf.issuer_role import issuer_role_import_for_schema

    egf = EgfDocument.from_sad(_egf_sad())
    attestation = "E" + "M" * 43
    imp = issuer_role_import_for_schema(egf, _Resolver(_templates()), attestation)
    assert imp["expected_schema_said"] != attestation


def test_zero_producers_raises_rather_than_returning_none():
    from keri_serviceaid.egf.issuer_role import (IssuerRoleUnresolvable,
                                                 issuer_role_import_for_schema)

    egf = EgfDocument.from_sad(_egf_sad())
    with pytest.raises(IssuerRoleUnresolvable) as ex:
        issuer_role_import_for_schema(egf, _Resolver(_templates()), "E" + "Z" * 43)
    assert "0 micro-app" in str(ex.value)


def test_two_producers_of_the_same_schema_raises():
    from keri_serviceaid.egf.issuer_role import (IssuerRoleUnresolvable,
                                                 issuer_role_import_for_schema)

    templates = _templates()
    templates["E" + "1" * 43]["credentials"]["exports"].append(
        {"id": "dupe", "schema": {"schema_said": "E" + "P" * 43}})
    egf = EgfDocument.from_sad(_egf_sad())
    with pytest.raises(IssuerRoleUnresolvable) as ex:
        issuer_role_import_for_schema(egf, _Resolver(templates), "E" + "P" * 43)
    assert "2 micro-app" in str(ex.value)


def test_a_producing_role_with_no_role_credential_raises():
    from keri_serviceaid.egf.issuer_role import (IssuerRoleUnresolvable,
                                                 issuer_role_import_for_schema)

    sad = _egf_sad()
    sad["credentials"] = [c for c in sad["credentials"] if c["id"] != "actuary_role"]
    sad["authorities"][0]["credentials"] = ["cuo_role"]
    egf = EgfDocument.from_sad(sad)
    with pytest.raises(IssuerRoleUnresolvable) as ex:
        issuer_role_import_for_schema(egf, _Resolver(_templates()), "E" + "P" * 43)
    assert "0 role credential" in str(ex.value)


def test_the_issuer_role_is_read_from_the_egf_not_hardcoded():
    """Domain neutrality: an ecosystem whose authority is not called 'admin' must
    still resolve. A literal 'admin' in the walk passes every other test here."""
    from keri_serviceaid.egf.issuer_role import issuer_role_import_for_schema

    sad = _egf_sad()
    for c in sad["credentials"]:
        c["issuer_role"] = "registrar"
    sad["authorities"][0]["role_id"] = "registrar"
    egf = EgfDocument.from_sad(sad)
    imp = issuer_role_import_for_schema(egf, _Resolver(_templates()), "E" + "P" * 43)
    assert imp["expected_issuer_role"] == "registrar"


def test_it_is_an_EgfError_so_run_verify_maps_it_to_exit_2():
    """AuthorityUnknown's trap: a bare Exception escapes run_verify's handlers as an
    uncaught traceback with a misleading exit code."""
    from keri_serviceaid.egf.errors import EgfError
    from keri_serviceaid.egf.issuer_role import IssuerRoleUnresolvable

    assert issubclass(IssuerRoleUnresolvable, EgfError)
