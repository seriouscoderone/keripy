from keri.vdr import credentialing
from serviceaid.issuing import ensure_registry, issue_grant


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


def test_ensure_registry_is_idempotent(issuer_hby, rating_schema):
    hab = issuer_hby.makeHab(name="svc", transferable=True)
    rgy = credentialing.Regery(hby=issuer_hby, name="svc", temp=True)
    r1 = ensure_registry(issuer_hby, hab, rgy, name="svc")
    r2 = ensure_registry(issuer_hby, hab, rgy, name="svc")
    assert r1.regk == r2.regk
