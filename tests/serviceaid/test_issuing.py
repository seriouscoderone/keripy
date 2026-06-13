from keri.app.habbing import Habery
from keri.app.notifying import Notifier
from keri.core import parsing, serdering
from keri.core.signing import Salter
from keri.kering import Vrsn_1_0
from keri.peer import exchanging
from keri.vc import protocoling
from keri.vdr import credentialing

from keri_cdk.handlers.serviceaid.issuing import ensure_registry, issue_grant


def test_issue_grant_produces_verifiable_acdc(issuer_hby, rating_schema, recipient_pre):
    said, sad = rating_schema
    hab = issuer_hby.makeHab(name="svc", transferable=True)  # no wits in unit test
    rgy = credentialing.Regery(hby=issuer_hby, name="svc", temp=True)
    registry = ensure_registry(issuer_hby, hab, rgy, name="svc")

    grant = issue_grant(
        issuer_hby, hab, rgy,
        schema_said=said,
        recipient=recipient_pre,
        attributes={"score": 720},
        registry_name="svc",
    )

    # The grant is a CESR-framed IPEX /ipex/grant exn carrying the ACDC.
    assert isinstance(grant, (bytes, bytearray))
    assert b"/ipex/grant" in bytes(grant)

    # The credential was issued and saved in the registry.
    saiders = list(rgy.reger.schms.get(keys=(said,)))
    assert len(saiders) == 1
    creder = rgy.reger.creds.get(keys=(saiders[0].qb64,))
    assert creder is not None
    assert creder.attrib["score"] == 720
    assert creder.attrib["i"] == recipient_pre


def test_grant_round_trips_through_recipient_parser(issuer_hby, rating_schema, recipient_pre):
    """The grant must be a well-formed CESR stream a recipient wallet accepts.

    A fresh recipient-side Habery first learns the issuer's KEL (as OOBI
    resolution would), then the grant is fed through the recipient's
    Parser/Exchanger stack. Acceptance (signature verification included) is
    proven by the exn landing in the recipient's exns database.
    """
    said, _ = rating_schema
    hab = issuer_hby.makeHab(name="svc", transferable=True)
    rgy = credentialing.Regery(hby=issuer_hby, name="svc", temp=True)

    grant = issue_grant(
        issuer_hby, hab, rgy,
        schema_said=said,
        recipient=recipient_pre,
        attributes={"score": 720},
        registry_name="svc",
    )

    # The first message in the stream is the grant exn itself.
    exn = serdering.SerderKERI(raw=bytes(grant))
    assert exn.ked["r"] == "/ipex/grant"
    assert {"acdc", "iss", "anc"} <= set(exn.ked["e"])

    rhby = Habery(name="verify", temp=True, salt=Salter(raw=b'abcdef0123456789').qb64)
    try:
        # Recipient learns the issuer's KEL so the exn signature can verify.
        parsing.Parser(kvy=rhby.kvy, version=Vrsn_1_0).parse(ims=hab.replay())
        rhby.kvy.processEscrows()
        assert hab.pre in rhby.kevers

        notifier = Notifier(rhby)
        exc = exchanging.Exchanger(hby=rhby, handlers=[])
        protocoling.loadHandlers(rhby, exc=exc, notifier=notifier)
        parsing.Parser(exc=exc, version=Vrsn_1_0).parseOne(ims=bytearray(grant))
        exc.processEscrow()

        stored = rhby.db.exns.get(keys=(exn.said,))
        assert stored is not None, "grant exn was not accepted by recipient Exchanger"
        assert stored.ked["r"] == "/ipex/grant"
        assert stored.ked["a"]["i"] == recipient_pre
    finally:
        rhby.close()


def test_ensure_registry_is_idempotent(issuer_hby, rating_schema):
    hab = issuer_hby.makeHab(name="svc", transferable=True)
    rgy = credentialing.Regery(hby=issuer_hby, name="svc", temp=True)
    r1 = ensure_registry(issuer_hby, hab, rgy, name="svc")
    r2 = ensure_registry(issuer_hby, hab, rgy, name="svc")
    assert r1.regk == r2.regk
