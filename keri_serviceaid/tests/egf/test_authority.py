"""expected_issuer_role -> authorities[].aid. The wire §14.3 found missing.

The resolver delegates role+phase filtering to the REAL
EgfDocument.authorities(role_id, context=None, accept_phases=('production',)) -> list
(documents.py:243), which defaults to production-only. The stand-in below mirrors that
exact signature so the test exercises the real contract; prefer a real
EgfDocument.from_sad(...) where a minimal valid EGF sad is available."""
import pytest

from keri_serviceaid.egf.authority import (
    AuthorityUnknown, authority_aid_for_role, credential_req_for_import,
)
from keri_serviceaid.egf.documents import EgfDocument
from keri_serviceaid.tests.egf.fixtures.make_fixture_egf import apply_mode_egf

ADMIN_AID = "EGjm-X1JMz-yKFeumEZ9meSVNvnV8VTXmjJMlyBVMMTO"
OTHER_AID = "EOtherAuthorityOtherAuthorityOtherAuthorit"
SCHEMA = "ESchemaSAIDSchemaSAIDSchemaSAIDSchemaSAIDSc"


class _Authority:
    def __init__(self, role_id, aid, phase="production"):
        self.role_id, self.aid, self.phase = role_id, aid, phase


class _Egf:
    """Mirrors EgfDocument.authorities(role_id, context=None, accept_phases=('production',))
    -> list — role-filtered AND phase-filtered, exactly as documents.py:243-259."""
    def __init__(self, authorities):
        self._authorities = tuple(authorities)

    def authorities(self, role_id, context=None, accept_phases=("production",)):
        return [a for a in self._authorities
                if a.role_id == role_id and a.phase in tuple(accept_phases)]


def test_a_declared_role_resolves_to_its_authority_aid():
    egf = _Egf([_Authority("admin", ADMIN_AID)])
    assert authority_aid_for_role(egf, "admin") == ADMIN_AID


def test_an_undeclared_role_raises_rather_than_returning_None():
    """Returning None would flow into CredentialReq.issuer, where credgate's
    `if cred_req.issuer and ...` treats falsy as 'no constraint' and the check
    silently stops constraining anything. That is the failure mode this raise exists
    to prevent."""
    egf = _Egf([_Authority("admin", ADMIN_AID)])
    with pytest.raises(AuthorityUnknown, match="regulator"):
        authority_aid_for_role(egf, "regulator")


def test_a_bootstrap_only_authority_is_NOT_resolved_by_default():
    """The phase posture, stated: the verifier pins a PRODUCTION issuer. A provisional
    (bootstrap) authority is not a root of trust to accept an arriving fact under — it
    must be promoted to production first. Same accept_phases discipline select_authority
    uses (onboarding.py). This is the reader the egf-authority-binding backlog's
    'open now, closed later' case turns on, so it is pinned here rather than left implicit."""
    egf = _Egf([_Authority("admin", ADMIN_AID, phase="bootstrap")])
    with pytest.raises(AuthorityUnknown, match="admin"):
        authority_aid_for_role(egf, "admin")


def test_an_ambiguous_role_raises_rather_than_guessing():
    """Zero and >1 are equally unusable to a caller that needs exactly one issuer —
    matching select_authority's fail-closed posture."""
    egf = _Egf([_Authority("admin", ADMIN_AID), _Authority("admin", OTHER_AID)])
    with pytest.raises(AuthorityUnknown):
        authority_aid_for_role(egf, "admin")


def test_an_authority_with_no_aid_raises_for_the_same_reason():
    egf = _Egf([_Authority("admin", "")])
    with pytest.raises(AuthorityUnknown, match="no aid"):
        authority_aid_for_role(egf, "admin")


def test_an_import_declaring_expected_issuer_role_produces_a_pinned_CredentialReq():
    egf = _Egf([_Authority("admin", ADMIN_AID)])
    req = credential_req_for_import(
        egf, {"expected_schema_said": SCHEMA, "expected_issuer_role": "admin"})
    assert req.schema == SCHEMA
    assert req.issuer == ADMIN_AID


def test_an_import_with_NO_expected_issuer_role_produces_an_unpinned_req_deliberately():
    """Not every import constrains its issuer, and forging one would be worse than
    leaving it open. The caller sees issuer is None and can decide."""
    egf = _Egf([_Authority("admin", ADMIN_AID)])
    req = credential_req_for_import(egf, {"expected_schema_said": SCHEMA})
    assert req.issuer is None


def test_a_declared_role_resolves_against_a_REAL_EgfDocument_not_just_the_stand_in():
    """`_Egf`/`_Authority` above are verified byte-for-byte against
    `EgfDocument.authorities` (documents.py:243-259), but this exercises the genuine
    class end-to-end through a real, meta-schema-valid egf-doc/0.1 SAD — the
    'Before you begin' instruction's preference where one is available.
    `apply_mode_egf(None)` (make_fixture_egf.py) declares exactly one production
    authority for role 'admin'."""
    egf = EgfDocument.from_sad(apply_mode_egf(None))
    assert authority_aid_for_role(egf, "admin") == "E" + "B" * 43
