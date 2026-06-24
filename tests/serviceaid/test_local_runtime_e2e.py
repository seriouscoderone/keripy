"""End-to-end (headless): a broker-gated /rate command issues a signed Quote.

Admits the gating credential into the runtime's reger via the proven issuance
path (standing in for live IPEX present-then-cache, which Plan 2 integration-tests
in the wallet), then drives a /rate command and asserts a Quote grant is delivered.
A FakeResolver stubs endpoint resolution (no real end-roles in test Haberies); the
FakeDeliverer captures the REAL issued grant bytes."""
import pytest

from keri.core import scheming
from keri.kering import Kinds
from keri.peer import exchanging
from keri.vdr import credentialing

from keri_serviceaid import (ServiceAid, Reply, CredentialReq, LocalRuntime,
                             IpexGrantIssuer, Endpoint)

from _schema import RATING_SCHEMA_SAD, BROKER_SCHEMA_SAD


class FakeResolver:
    def resolve(self, sender, hby):
        return Endpoint(role="mailbox", eid=sender, url="http://mbx/")


class FakeDeliverer:
    def __init__(self):
        self.delivered = []

    def deliver(self, msg, endpoint, ctx):
        self.delivered.append(msg)


def _register(hby, sad):
    schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
    hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    return schemer.said


def _build(issuer_hby, broker_said, quote_said):
    hab = issuer_hby.makeHab(name="rating-engine")
    rgy = credentialing.Regery(hby=issuer_hby, name="rating-engine", temp=True)
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/rate", issues=quote_said,
                 requires_credential=CredentialReq(schema=broker_said))
    def rate(req):
        return Reply.acdc(recipient=req.sender,
                          attributes={"i": req.sender, "score": 7})

    fake = FakeDeliverer()
    svc.deliverer = fake
    svc.resolver = FakeResolver()
    rt = LocalRuntime(svc, hby=issuer_hby, hab=hab, rgy=rgy)
    return hab, rgy, rt, fake


def test_gated_rate_issues_quote(issuer_hby, recipient_pre):
    broker_said = _register(issuer_hby, BROKER_SCHEMA_SAD)
    quote_said = _register(issuer_hby, RATING_SCHEMA_SAD)
    hab, rgy, rt, fake = _build(issuer_hby, broker_said, quote_said)

    # Admit the broker credential for recipient_pre into the runtime's reger.
    IpexGrantIssuer()._issue_grant(
        issuer_hby, hab, rgy, schema_said=broker_said, recipient=recipient_pre,
        attributes={"license": "B-123"}, registry_name="rating-engine")

    exn, _ = exchanging.exchange(route="/rate", sender=recipient_pre,
                                 receiver=hab.pre, attributes={"coverage": "auto"})
    rt._captures["/rate"].handle(exn, attachments=[])
    rt.process_captured()

    assert len(fake.delivered) == 1          # gated request -> signed Quote grant


def test_gated_rate_denied_without_credential(issuer_hby, recipient_pre):
    broker_said = _register(issuer_hby, BROKER_SCHEMA_SAD)
    quote_said = _register(issuer_hby, RATING_SCHEMA_SAD)
    hab, rgy, rt, fake = _build(issuer_hby, broker_said, quote_said)

    # No credential admitted -> CredentialGate denies -> silent drop.
    exn, _ = exchanging.exchange(route="/rate", sender=recipient_pre,
                                 receiver=hab.pre, attributes={"coverage": "auto"})
    rt._captures["/rate"].handle(exn, attachments=[])
    rt.process_captured()

    assert fake.delivered == []              # denied -> nothing issued or delivered
