"""IpexGrantIssuer issues an ACDC and returns a self-contained IPEX /ipex/grant."""
from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import scheming, parsing, serdering
from keri.kering import Kinds, Vrsn_1_0
from keri.vdr import credentialing

from keri_serviceaid import IpexGrantIssuer, Reply, Context


RATING_SCHEMA_SAD = {
    "$id": "", "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Gated Record", "type": "object",
    "credentialType": "GatedRecord",
    "properties": {
        "v": {"type": "string"}, "d": {"type": "string"}, "u": {"type": "string"},
        "i": {"type": "string"}, "ri": {"type": "string"}, "s": {"type": "string"},
        "a": {"type": "object",
              "properties": {"d": {"type": "string"}, "i": {"type": "string"},
                             "dt": {"type": "string"}, "data": {"type": "string"}},
              "required": ["d", "i", "dt"]},
    },
    "required": ["v", "d", "i", "ri", "s", "a"],
}


def test_grant_shape_is_ipex_grant_exn():
    hby = Habery(name="svc", temp=True, salt=Salter(raw=b'0123456789abcdef').qb64)
    hab = hby.makeHab(name="svc", transferable=True)

    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)
    hby.db.schema.pin(keys=(schemer.said,), val=schemer)

    # recipient KEL must be known before issuing
    rcp_hby = Habery(name="rcp", temp=True, salt=Salter(raw=b'fedcba9876543210').qb64)
    rcp = rcp_hby.makeHab(name="rcp", transferable=True)
    parsing.Parser(kvy=hby.kvy, version=Vrsn_1_0).parse(ims=bytearray(rcp.replay()))
    hby.kvy.processEscrows()

    rgy = credentialing.Regery(hby=hby, name="svc", temp=True)
    ctx = Context(hby=hby, hab=hab, rgy=rgy, registry_name="svc")
    reply = Reply.acdc(recipient=rcp.pre, attributes={"data": "cool"})
    reply.schema_said = schemer.said

    grant = IpexGrantIssuer().issue(reply, ctx)
    assert isinstance(grant, (bytes, bytearray))
    serder = serdering.SerderKERI(raw=bytes(grant))
    assert serder.ked["t"] == "exn"
    assert serder.ked["r"] == "/ipex/grant"

    hby.close(); rcp_hby.close()
