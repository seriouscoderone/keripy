"""LocalRuntime wires local providers, registers capture handlers, and drains
them through the pipeline. Uses a fake deliverer to capture the issued grant."""
import pytest

from keri.core import scheming
from keri.kering import Kinds
from keri.peer import exchanging
from keri.vdr import credentialing

from keri_serviceaid import (ServiceAid, Reply, LocalRuntime, BoundResolver,
                             LMDBLedger, CredentialGate, OracleVerifier,
                             IpexGrantIssuer, PostmanDeliverer)

from _schema import RATING_SCHEMA_SAD


class FakeDeliverer:
    def __init__(self):
        self.delivered = []

    def deliver(self, msg, endpoint, ctx):
        self.delivered.append((msg, endpoint))


class FakeResolver:
    """Stub resolver: returns a dummy Endpoint without needing real endsFor data."""
    def resolve(self, sender, hby):
        from keri_serviceaid import Endpoint
        return Endpoint(role="mailbox", eid=sender, url="https://stub")


@pytest.fixture
def quote_schema(issuer_hby):
    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)
    issuer_hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    return schemer.said


def test_localruntime_wires_local_providers(issuer_hby):
    hab = issuer_hby.makeHab(name="rating-engine")
    rgy = credentialing.Regery(hby=issuer_hby, name="rating-engine", temp=True)
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/ping")
    def ping(req):
        return Reply.none()

    rt = LocalRuntime(svc, hby=issuer_hby, hab=hab, rgy=rgy)

    assert isinstance(svc.resolver, BoundResolver)
    assert isinstance(svc.idempotency, LMDBLedger)
    assert isinstance(svc.authz, CredentialGate)
    assert isinstance(svc.verifier, OracleVerifier)
    assert isinstance(svc.issuer, IpexGrantIssuer)
    assert isinstance(svc.deliverer, PostmanDeliverer)
    assert "/ping" in issuer_hby.exc.routes


def test_localruntime_preserves_preset_providers(issuer_hby):
    hab = issuer_hby.makeHab(name="rating-engine")
    rgy = credentialing.Regery(hby=issuer_hby, name="rating-engine", temp=True)
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/ping")
    def ping(req):
        return Reply.none()

    sentinel_authz = object()      # identity check only; LocalRuntime guards on `is None`
    svc.authz = sentinel_authz
    LocalRuntime(svc, hby=issuer_hby, hab=hab, rgy=rgy)
    assert svc.authz is sentinel_authz      # pre-set provider not overwritten


def test_localruntime_processes_captured_command_and_delivers(issuer_hby, quote_schema, recipient_pre):
    hab = issuer_hby.makeHab(name="rating-engine")
    rgy = credentialing.Regery(hby=issuer_hby, name="rating-engine", temp=True)
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/rate", issues=quote_schema)     # ungated; open allowlist
    def rate(req):
        return Reply.acdc(recipient=req.sender,
                          attributes={"i": req.sender, "score": 42})

    fake = FakeDeliverer()
    svc.deliverer = fake
    svc.resolver = FakeResolver()   # no real endpoint registration in test Habery
    rt = LocalRuntime(svc, hby=issuer_hby, hab=hab, rgy=rgy)

    # Build a /rate command exn from recipient_pre, addressed to the bound hab.
    exn, _end = exchanging.exchange(route="/rate", sender=recipient_pre,
                                    receiver=hab.pre, attributes={"coverage": "auto"})
    # Inject as a verified capture (headless analogue of the live mailbox path).
    rt._captures["/rate"].handle(exn, attachments=[])
    rt.process_captured()

    assert len(fake.delivered) == 1     # one Quote grant delivered


def test_runtime_exposes_inbound_contract_for_host_injected_poller(issuer_hby):
    """A Service-AID is hosted by a node, not a node itself: the runtime does NOT
    build a mailbox poller. It exposes the contract the host injects one against:
    command_topics (derived from routes), the cred_verifier the poller wires for
    IPEX admit, and capture handlers registered on hby.exc (where a poller feeds)."""
    hab = issuer_hby.makeHab(name="rating-engine")
    rgy = credentialing.Regery(hby=issuer_hby, name="rating-engine", temp=True)
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/insurance/cmd/grant_license")
    def grant(req):
        return Reply.none()

    rt = LocalRuntime(svc, hby=issuer_hby, hab=hab, rgy=rgy)

    # command topic = first route segment (keripy Postman forwarding default)
    assert rt.command_topics == ["insurance"]
    # cred_verifier exposed for the host to wire into its injected poller
    assert rt.cred_verifier is not None
    # capture handler registered on the Habery's exchanger (where a poller feeds)
    assert "/insurance/cmd/grant_license" in issuer_hby.exc.routes
    # the runtime no longer manufactures transport — that is the host's job
    assert not hasattr(rt, "mailbox_doer")


class RaisingResolver:
    def resolve(self, sender, hby):
        raise LookupError("no endpoint")


def test_process_captured_suppresses_per_exn_errors(issuer_hby, quote_schema, recipient_pre):
    hab = issuer_hby.makeHab(name="rating-engine")
    rgy = credentialing.Regery(hby=issuer_hby, name="rating-engine", temp=True)
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/rate", issues=quote_schema)
    def rate(req):
        return Reply.acdc(recipient=req.sender, attributes={"i": req.sender, "score": 1})

    svc.resolver = RaisingResolver()
    rt = LocalRuntime(svc, hby=issuer_hby, hab=hab, rgy=rgy)

    exn, _ = exchanging.exchange(route="/rate", sender=recipient_pre,
                                 receiver=hab.pre, attributes={})
    rt._captures["/rate"].handle(exn, attachments=[])
    rt.process_captured()   # must NOT raise despite the resolver LookupError
