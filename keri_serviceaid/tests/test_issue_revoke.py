"""Real-stack test for IpexGrantIssuer.revoke (§6.1).

Drives a real KERI TEL `rev` event end-to-end against a temp Habery + a
no-backer Regery: issue an ACDC, then revoke it, and assert the credential's
TEL state becomes `rev` (no-backer) / `brv` (witnessed). v1 is no-backer, so
this completes in-process on a virtual-time Doist with no witness receipts.
"""
from keri.app import habbing
from keri.core import scheming
from keri.kering import Kinds, Ilks
from keri.vdr import credentialing

from keri_serviceaid.contract import Reply
from keri_serviceaid.providers.issue import Context, IpexGrantIssuer, ensure_registry


def _stack():
    hby = habbing.Habery(name="rev", temp=True, free=True)
    hab = hby.makeHab(name="rev", transferable=True, wits=[], toad=0)
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
