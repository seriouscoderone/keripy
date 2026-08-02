"""Real-stack test for IpexGrantIssuer.revoke (§6.1).

Drives a real KERI TEL `rev` event end-to-end against a temp Habery + a
no-backer Regery: issue an ACDC, then revoke it, and assert the credential's
TEL state becomes `rev` (no-backer) / `brv` (witnessed). v1 is no-backer, so
this completes in-process on a virtual-time Doist with no witness receipts.
"""
import pytest

from keri.app import habbing
from keri.core import scheming
from keri.kering import Kinds, Ilks, Vrsn_1_0
from keri.vdr import credentialing
from keri.vdr import eventing as teventing

from keri_serviceaid.contract import Reply
from keri_serviceaid.providers.issue import (
    Context, IpexGrantIssuer, ensure_registry, issue_credential, revoke_credential,
)


def _stack():
    hby = habbing.Habery(name="rev", temp=True, free=True)
    # v1-pinned for the same reason as the `issue_env` fixture below: the TEL
    # registry's `vcp` inception is v1-only, and Registry.make inherits the
    # hab's pvrsn — an unpinned hab now defaults to v2, so ensure_registry
    # raises SerializeError("Invalid packet type (ilk) = vcp").
    hab = hby.makeHab(name="rev", transferable=True, wits=[], toad=0,
                      version=Vrsn_1_0)
    # temp=True so the Reger shares the temp Habery's ephemeral lifecycle. A
    # persistent Reger keyed by a fixed name collides across runs: a fresh temp
    # Habery mints a NEW hab.pre each run, but the on-disk registry record keeps
    # the old prefix, so loadRegistries() KeyErrors on the missing hab.
    rgy = credentialing.Regery(hby=hby, name="rev", temp=True)
    ensure_registry(hby, hab, rgy, name="rev")
    return hby, hab, rgy


def _register_schema(hby):
    sed = {
        "$id": "",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "d": {"type": "string"},
            "i": {"type": "string"},
            "a": {"type": "object"},
        },
    }
    schemer = scheming.Schemer(sed=sed, kind=Kinds.json)
    hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    return schemer


def test_revoke_marks_tel_revoked():
    hby, hab, rgy = _stack()
    schemer = _register_schema(hby)

    iss = IpexGrantIssuer()
    ctx = Context(hby=hby, hab=hab, rgy=rgy, registry_name="rev")

    grant = iss.issue(
        Reply(kind="acdc", recipient=hab.pre, attributes={"a": 1},
              schema_said=schemer.said),
        ctx,
    )
    assert grant

    reg = rgy.registryByName("rev")
    # Recover the issued credential SAID: schms maps schema-said -> cred-saids.
    said = [s for s in rgy.reger.schms.get(keys=schemer.said.encode())][0].qb64

    # Sanity: TEL state is `iss` before revoke.
    tever = rgy.reger.tevers[reg.regk]
    assert tever.vcState(said).et == Ilks.iss

    out = iss.revoke(
        Reply.revoke(recipient=hab.pre, credential_said=said),
        ctx,
    )
    assert out

    # The substantive assertion: after revoke, TEL state is rev (no-backer) / brv.
    assert tever.vcState(said).et in (Ilks.rev, Ilks.brv)


@pytest.fixture
def issue_env():
    """In-process (hby, hab, rgy) + schema_said + attributes, v1-pinned.

    Mirrors the proven real-Habery recipe in tests/serviceaid/conftest.py
    (`issuer_hby`/`rating_schema`) + test_self_issue_and_grant.py, which
    drives `issue_credential` directly against a real Habery/Regery.
    keri_serviceaid's TRANSITIONAL v1 hold (see providers/issue.py's module
    docstring) means an unpinned Habery/Hab defaults to KERI v2 today, and
    the TEL registry's `vcp` inception event is v1-only — so the hab here is
    explicitly incepted at Vrsn_1_0 (this file's existing `_stack()` helper
    predates that default flipping to v2 and is not used here for that
    reason: it no longer completes `ensure_registry` on its own).
    """
    hby = habbing.Habery(name="issue-revoke", temp=True, free=True)
    hab = hby.makeHab(name="issue-revoke", transferable=True, wits=[], toad=0,
                      version=Vrsn_1_0)
    rgy = credentialing.Regery(hby=hby, name="issue-revoke", temp=True)
    schemer = _register_schema(hby)
    attributes = {"a": {"score": 1}}
    yield hby, hab, rgy, schemer.said, attributes
    hby.close()


def test_revoke_credential_flips_tel_state_to_revoked(issue_env):
    # issue_env: (hby, hab, rgy, schema_said, attributes) — mirror test_issue.py's fixture.
    hby, hab, rgy, schema_said, attributes = issue_env
    said = issue_credential(
        hby, hab, rgy, schema_said=schema_said, recipient=hab.pre,
        attributes=attributes, registry_name="svc")
    creder = rgy.reger.cloneCred(said=said)[0]
    tever = rgy.reger.tevers[creder.regid]
    assert tever.vcState(said).et in ("iss", "bis")   # active pre-revoke

    returned = revoke_credential(
        hby, hab, rgy, credential_said=said, registry_name="svc")

    assert returned == said
    assert tever.vcState(said).et in ("rev", "brv")   # revoked post-call
    # The rev is the second TEL event (single issuance -> rev at sn=1).
    assert tever.vcSn(said) == 1
