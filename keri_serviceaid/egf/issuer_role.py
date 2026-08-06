# -*- encoding: utf-8 -*-
"""Which role must have issued this attestation? — answered by the ecosystem, not an operator.

C1 left the counterparty-legitimacy check needing an operator to name the receiving
template and import (`--micro-app` + `--import-id`), because the check CANNOT be keyed on
the attestation's own schema: the import it needs names the ROLE credential (e.g.
`actuary_role`), which is deliberately not the attestation's schema. A GUI surface has
that receiving context; a stranger verifier does not, so C1's CLI fails loud.

This module supplies the missing hop, and it is an ECOSYSTEM fact rather than an operator
preference: a rate-program attestation is issued by the actuary, period.

    attestation schema
      -> the micro-app template that EXPORTS it   (via micro_apps[], each resolved
                                                   BY ITS COMMITTED SAID)
      -> that template's role.id
      -> the EGF credential whose holder_role is that role
      -> {its schema_said, its issuer_role}

EXPORTS, not imports: a schema is imported by every downstream role that consumes it, so
keying on imports would find several "producers" for a widely-read credential. Only the
exporter issues it.

Resolving templates by SAID keeps "one hash to trust" transitive: a `micro_apps[]` entry
is a commitment, so following it is hash-safe, and `resolve_micro_app` re-derives the SAID
and fails closed. This is the R3 ruling — verifier logic over committed refs, NOT a
materialised EGF field.

FAILS CLOSED ON AMBIGUITY. Zero and several both raise, for the reason
`authority_aid_for_role` raises: a verifier that must pin exactly one expected issuer
cannot act on "none" or "either", and returning None would reach `credential_req_for_import`
as an UNPINNED requirement — degrading the check to "holds any credential of this schema,
from anyone", the exact fail-open hole C1's Task 8 review closed.
"""
from __future__ import annotations

from keri_serviceaid.egf.errors import EgfError


class IssuerRoleUnresolvable(EgfError):
    """The ecosystem does not name exactly one role that issues this schema.

    Subclasses `EgfError` deliberately: concierge's `run_verify` catches `EgfError` and
    maps it to exit 2. A bare `Exception` would escape as an uncaught traceback — the
    trap `AuthorityUnknown` already fell into.
    """


def issuer_role_import_for_schema(egf, resolver, schema_said: str) -> dict:
    """The credential import naming the expected issuer role for an attestation of
    `schema_said`, in the shape `issuer_holds_expected_role` consumes:

        {"expected_schema_said": <the ROLE credential's own schema>,
         "expected_issuer_role": <the role whose authority issues that credential>}
    """
    producers = []
    for ref in egf.micro_apps():
        template = resolver.resolve_micro_app(ref.said)
        credentials = template.get("credentials")
        if not isinstance(credentials, dict):
            # `resolve_micro_app` does no shape validation (unlike resolve_egf and
            # resolve_schema). A SAD with no credentials block is malformed, not a
            # silent non-match — say so rather than counting it as "no export".
            raise IssuerRoleUnresolvable(
                f"micro-app {ref.said} declares no credentials block; it cannot be "
                "searched for the exporter of a schema")
        exports = credentials.get("exports") or []
        if any((e.get("schema") or {}).get("schema_said") == schema_said
               for e in exports):
            producers.append((ref, template))

    if len(producers) != 1:
        raise IssuerRoleUnresolvable(
            f"schema {schema_said!r} is exported by {len(producers)} micro-app "
            "template(s) in this ecosystem; exactly one is required to name the "
            "expected issuer role, and zero or several are equally unusable to a "
            "verifier that must pin exactly one issuer")

    ref, template = producers[0]
    producing_role = (template.get("role") or {}).get("id")
    if not producing_role:
        raise IssuerRoleUnresolvable(
            f"micro-app {ref.said} exports {schema_said!r} but declares no role.id; "
            "without the producing role there is no role credential to require")

    held = egf.credentials_for_holder(producing_role)
    if len(held) != 1:
        raise IssuerRoleUnresolvable(
            f"role {producing_role!r} — which issues {schema_said!r} — holds "
            f"{len(held)} role credential(s) in this EGF; exactly one is required to "
            "name the credential that legitimises them. Zero means the ecosystem "
            "accepts a schema nobody is authorised to issue")

    credential = held[0]
    return {"expected_schema_said": credential.schema_said,
            "expected_issuer_role": credential.issuer_role}
