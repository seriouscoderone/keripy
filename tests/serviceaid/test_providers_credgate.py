"""CredentialGate enforces requires_credential against held (presented) ACDCs.

The gating credential is issued to `recipient_pre` (the issuee/presenter) into the
gate's own reger via the proven IpexGrantIssuer issuance path, then queried by the
gate. (Live present-then-cache admission over IPEX is covered by Plan 2's
integration test; here we exercise the gate's query + revocation logic.)"""
import pytest

from keri.core import scheming, coring, serdering
from keri.core.signing import Salter
from keri.db.dbing import dgKey
from keri.kering import Kinds, Ilks
from keri.vdr import credentialing

from keri_serviceaid import (ServiceAid, Request, CredentialReq, CredentialGate,
                             IpexGrantIssuer)

from _schema import BROKER_SCHEMA_SAD, RATING_SCHEMA_SAD


@pytest.fixture
def broker_schema(issuer_hby):
    schemer = scheming.Schemer(sed=dict(BROKER_SCHEMA_SAD), kind=Kinds.json)
    issuer_hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    return schemer.said


@pytest.fixture
def quote_schema(issuer_hby):
    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)
    issuer_hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    return schemer.said


@pytest.fixture
def gated_svc(broker_schema, quote_schema):
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/rate", issues=quote_schema,
                 requires_credential=CredentialReq(schema=broker_schema))
    def rate(req):
        ...

    @svc.command(route="/ping")          # ungated -> base Allowlist
    def ping(req):
        ...
    return svc


@pytest.fixture
def gate_with_broker_cred(issuer_hby, recipient_pre, broker_schema, gated_svc):
    """Issue a broker license to recipient_pre into the gate's reger, return
    (gate, rgy, auth_hab) for follow-on revocation."""
    auth = issuer_hby.makeHab(name="auth")
    rgy = credentialing.Regery(hby=issuer_hby, name="concierge", temp=True)
    IpexGrantIssuer()._issue_grant(
        issuer_hby, auth, rgy,
        schema_said=broker_schema, recipient=recipient_pre,
        attributes={"license": "B-123"}, registry_name="concierge")
    gate = CredentialGate(hby=issuer_hby, reger=rgy.reger, svc=gated_svc)
    return gate, rgy, auth


def test_allows_holder_of_valid_credential(gate_with_broker_cred, recipient_pre):
    gate, _, _ = gate_with_broker_cred
    allow, reason = gate.authorize(
        Request(sender=recipient_pre, route="/rate", payload={}, message_said="Ex"))
    assert allow is True, reason


def test_denies_sender_without_credential(gate_with_broker_cred):
    gate, _, _ = gate_with_broker_cred
    allow, reason = gate.authorize(
        Request(sender="EsomeOtherAID", route="/rate", payload={}, message_said="Ey"))
    assert allow is False
    assert "EsomeOtherAID" in reason


def test_ungated_command_falls_through_to_base_allowlist(gate_with_broker_cred):
    gate, _, _ = gate_with_broker_cred
    allow, _ = gate.authorize(
        Request(sender="EanyAID", route="/ping", payload={}, message_said="Ez"))
    assert allow is True


def test_denies_when_issuer_mismatches(issuer_hby, recipient_pre, broker_schema,
                                       quote_schema):
    """A valid broker cred is held, but the command pins a DIFFERENT issuer AID,
    so the gate must reject it via the `creder.issuer != cred_req.issuer` branch."""
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/rate", issues=quote_schema,
                 requires_credential=CredentialReq(
                     schema=broker_schema,
                     issuer="ENotTheActualIssuerAIDxxxxxxxxxxxxxxxxxxxxxxx"))
    def rate(req):
        ...

    auth = issuer_hby.makeHab(name="auth")
    rgy = credentialing.Regery(hby=issuer_hby, name="concierge", temp=True)
    IpexGrantIssuer()._issue_grant(
        issuer_hby, auth, rgy,
        schema_said=broker_schema, recipient=recipient_pre,
        attributes={"license": "B-123"}, registry_name="concierge")
    gate = CredentialGate(hby=issuer_hby, reger=rgy.reger, svc=svc)

    allow, reason = gate.authorize(
        Request(sender=recipient_pre, route="/rate", payload={}, message_said="Ev"))
    assert allow is False, reason


def test_denies_after_revocation(gate_with_broker_cred, recipient_pre):
    gate, rgy, auth = gate_with_broker_cred
    saids = {s.qb64 for s in rgy.reger.schms.get(
        keys=gate.svc.lookup("/rate").requires_credential.schema.encode("utf-8"))}
    subj = [s for s in rgy.reger.subjs.get(keys=recipient_pre.encode("utf-8"))
            if s.qb64 in saids]
    said = subj[0].qb64
    reg = rgy.registryByName("concierge")

    # Registry.revoke() creates the rev TEL event in the anchorless escrow (taes).
    rev = reg.revoke(said=said)
    rseal = dict(i=rev.pre, s=rev.snh, d=rev.said)

    # The anchor ixn must happen in the KEL, then its seqner/saider stored in
    # reger.ancs so processEscrowAnchorless can verify the anchor and commit the
    # rev event to reger.tels (flipping vcState from iss → rev).
    anc_bytes = auth.interact(data=[rseal])
    anc_serder = serdering.SerderKERI(raw=bytes(anc_bytes))
    dgkey = dgKey(rev.pre, rev.said)
    rgy.reger.ancs.put(keys=dgkey,
                       val=(coring.Number(sn=anc_serder.sn),
                            coring.Diger(qb64=anc_serder.said)))
    rgy.tvy.processEscrowAnchorless()

    # Verify the Tever state actually flipped before asserting the gate
    tever = rgy.reger.tevers[reg.regk]
    state = tever.vcState(said)
    assert state is not None and state.et == Ilks.rev, (
        f"Revocation did not complete; vc state={state.et if state else None}")

    allow, reason = gate.authorize(
        Request(sender=recipient_pre, route="/rate", payload={}, message_said="Ew"))
    assert allow is False, reason
