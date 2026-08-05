# -*- encoding: utf-8 -*-
"""expected_issuer_role -> the EGF's authority AID -> CredentialReq.issuer.

Amendment C §14.3. The CHECK already exists: `providers/credgate.py:41` enforces
`if cred_req.issuer and creder.issuer != cred_req.issuer`, and `CredentialReq` carries
schema + issuer + issuee + a per-request TEL revocation re-check — all four conjuncts
of "this counterparty holds the expected role credential from the expected authority."

What was missing is this wire. `expected_issuer_role` is declared by all five corpus
bundles and appeared in shipped source exactly once, inside a docstring. A template
saying "I expect this from admin" reached nothing that could act on it.

WHY UNRESOLVABLE MUST RAISE. `credgate` reads `if cred_req.issuer and ...` — a falsy
issuer means "no constraint," so returning None on an unknown role would turn a
declared expectation into silence. That is the exact shape of defect the register calls
a check that stops tracking the model: it keeps passing while covering less. Zero,
ambiguous, and bootstrap-only all raise for the same reason — a verifier that pins an
issuer needs exactly one PRODUCTION authority, and the phase discipline is shared with
`select_authority` (`egf/onboarding.py`) rather than re-invented here.
"""
from __future__ import annotations

from keri_serviceaid.contract import CredentialReq


class AuthorityUnknown(Exception):
    """A role id has no single usable production authority in this EGF. Never softened
    to a None return: see the module docstring."""


def authority_aid_for_role(egf, role_id: str, *, accept_phases=("production",)) -> str:
    """The AID of the single authority the EGF names for `role_id` within `accept_phases`.

    Delegates role+phase filtering to `EgfDocument.authorities` (documents.py:243) and
    fails closed on zero-or-ambiguous, matching `select_authority`. The phase posture is
    explicit and defaults to production: a bootstrap authority is provisional and is not a
    root of trust to accept an arriving fact under. Zero and >1 are equally unusable to a
    caller that must pin exactly one issuer.
    """
    matches = egf.authorities(role_id, accept_phases=accept_phases)
    if len(matches) != 1:
        raise AuthorityUnknown(
            f"role {role_id!r} resolves to {len(matches)} authorities within phases "
            f"{tuple(accept_phases)!r}; a verifier needs exactly one issuer to pin, and "
            "zero or ambiguous are equally unusable (matching select_authority)")
    aid = getattr(matches[0], "aid", None)
    if not aid:
        raise AuthorityUnknown(
            f"authority for role {role_id!r} declares no aid; an unbound authority "
            "cannot pin an issuer, and pinning nothing would read as 'any issuer'")
    return aid


def credential_req_for_import(egf, imp: dict) -> CredentialReq:
    """Build the inbound requirement a template's credential import declares.

    `expected_issuer_role` is optional: an import that does not name one produces an
    unpinned requirement, and the caller can see `issuer is None` and decide. Forging
    an issuer would be worse than leaving it open.
    """
    role = imp.get("expected_issuer_role")
    return CredentialReq(
        schema=imp["expected_schema_said"],
        issuer=authority_aid_for_role(egf, role) if role else None,
    )
