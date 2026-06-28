"""Characterization test — pins the CredentialGate deny-path (§6.2).

Verifies that a caller holding NO matching credential is denied, with a
reason message identifying the absent schema.  This is a "verify, not
rebuild" test: it passes against the existing credgate.py implementation
and locks the §6.2 deny-path behaviour so future refactors cannot silently
drop it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from keri_serviceaid.contract import CredentialReq, Reply, ServiceAid
from keri_serviceaid.providers.credgate import CredentialGate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc(schema: str = "ESchemaX") -> ServiceAid:
    svc = ServiceAid(alias="doi")

    @svc.command(
        route="/x",
        issues="ESchemaX",
        requires_credential=CredentialReq(schema=schema),
    )
    def _fn(req):
        return Reply.none()

    return svc


class _FakeReger:
    """Minimal reger stub: `schms.get` and `subjs.get` return iterables."""

    def __init__(self, *, schms=(), subjs=()):
        self._schms = list(schms)
        self._subjs = list(subjs)

    @property
    def schms(self):
        return SimpleNamespace(get=lambda keys: iter(self._schms))

    @property
    def subjs(self):
        return SimpleNamespace(get=lambda keys: iter(self._subjs))


# ---------------------------------------------------------------------------
# §6.2 deny-path characterization test
# ---------------------------------------------------------------------------

def test_denies_when_no_held_credential():
    """§6.2: caller holding no matching credential MUST be denied.

    When reger.schms returns an empty iterable (no credential of the required
    schema held by the concierge), CredentialGate.authorize must return
    (False, reason) where reason mentions the absent schema.
    """
    svc = _svc(schema="ESchemaX")
    reger = _FakeReger(schms=[], subjs=[])   # nothing held

    gate = CredentialGate(hby=None, reger=reger, svc=svc)

    req = SimpleNamespace(route="/x", sender="ECaller")
    allow, reason = gate.authorize(req)

    assert allow is False, "gate must deny when no credential is held"
    assert "no held credential" in reason.lower(), (
        f"reason should mention absence of credential; got: {reason!r}"
    )
    assert "ESchemaX" in reason, (
        f"reason should name the required schema; got: {reason!r}"
    )
