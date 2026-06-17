"""Pipeline behavior with FAKE providers injected into a ServiceAid.

Asserts BEHAVIOR not HTTP: grant delivered to endpoint; deny→silence;
replay→re-deliver-not-re-issue; compute-raise→nothing recorded/issued."""
from types import SimpleNamespace

from keri_serviceaid import (ServiceAid, Reply, Request, KeyState, Endpoint,
                             VerificationError)
from keri_serviceaid import pipeline


# ---- fakes -----------------------------------------------------------------
class FakeVerifier:
    def __init__(self, raise_=False):
        self.raise_ = raise_

    def verify(self, sender, ims, hby):
        if self.raise_:
            raise VerificationError("tier unmet")
        return KeyState(pre=sender, tier="receipts")


class FakeAuthz:
    def __init__(self, allow=True):
        self.allow = allow

    def authorize(self, req):
        return (self.allow, "" if self.allow else "denied")


class FakeIssuer:
    def __init__(self):
        self.calls = 0

    def issue(self, reply, ctx):
        self.calls += 1
        return b"GRANT-" + reply.recipient.encode()


class FakeResolver:
    def resolve(self, sender, hby):
        return Endpoint(role="mailbox", eid="EMbx", url="https://mbx")


class FakeDeliverer:
    def __init__(self):
        self.delivered = []

    def deliver(self, msg, endpoint, ctx):
        self.delivered.append((bytes(msg), endpoint.eid))


class FakeLedger:
    def __init__(self):
        self.store = {}

    def seen(self, said):
        return self.store.get(said)

    def record(self, said, grant):
        self.store[said] = bytes(grant)


def _serder(route="/svc/cmd/go", sender="EReq", said="ESaid1", attrs=None):
    return SimpleNamespace(ked={"i": sender, "r": route, "a": attrs or {"k": "v"}},
                           said=said, raw=b"")


def _state(svc, ledger, issuer, resolver, deliverer, verifier, authz):
    svc.idempotency = ledger
    svc.issuer = issuer
    svc.resolver = resolver
    svc.deliverer = deliverer
    svc.verifier = verifier
    svc.authz = authz
    return SimpleNamespace(svc=svc, hby=object(), hab=object(), rgy=object(),
                           cfg=SimpleNamespace(alias="svc"))


def _svc_with_acdc_command():
    svc = ServiceAid(alias="svc")

    @svc.command(route="/svc/cmd/go", issues="ESchema")
    def go(req: Request) -> Reply:
        return Reply.acdc(recipient=req.sender, attributes=req.payload)
    return svc


def test_acdc_path_issues_records_and_delivers():
    issuer, resolver, deliverer, ledger = (FakeIssuer(), FakeResolver(),
                                           FakeDeliverer(), FakeLedger())
    state = _state(_svc_with_acdc_command(), ledger, issuer, resolver, deliverer,
                   FakeVerifier(), FakeAuthz(allow=True))
    pipeline.process(state, _serder(), attachments=[])
    assert issuer.calls == 1
    assert deliverer.delivered == [(b"GRANT-EReq", "EMbx")]
    assert ledger.seen("ESaid1") == b"GRANT-EReq"   # recorded BEFORE deliver


def test_deny_is_silent_no_issue_no_deliver():
    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    state = _state(_svc_with_acdc_command(), ledger, issuer, FakeResolver(),
                   deliverer, FakeVerifier(), FakeAuthz(allow=False))
    pipeline.process(state, _serder(), attachments=[])
    assert issuer.calls == 0 and deliverer.delivered == []
    assert ledger.seen("ESaid1") is None


def test_replay_redelivers_recorded_grant_not_reissue():
    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    ledger.record("ESaid1", b"PRIOR-GRANT")          # simulate a prior issuance
    state = _state(_svc_with_acdc_command(), ledger, issuer, FakeResolver(),
                   deliverer, FakeVerifier(), FakeAuthz(allow=True))
    pipeline.process(state, _serder(said="ESaid1"), attachments=[])
    assert issuer.calls == 0                          # NOT re-issued
    assert deliverer.delivered == [(b"PRIOR-GRANT", "EMbx")]   # re-delivered


def test_compute_raise_records_nothing():
    svc = ServiceAid(alias="svc")

    @svc.command(route="/svc/cmd/boom")
    def boom(req): raise RuntimeError("kaboom")

    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    state = _state(svc, ledger, issuer, FakeResolver(), deliverer,
                   FakeVerifier(), FakeAuthz(allow=True))
    pipeline.process(state, _serder(route="/svc/cmd/boom"), attachments=[])
    assert issuer.calls == 0 and deliverer.delivered == []
    assert ledger.store == {}


def test_bad_signature_tier_unmet_is_silent():
    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    state = _state(_svc_with_acdc_command(), ledger, issuer, FakeResolver(),
                   deliverer, FakeVerifier(raise_=True), FakeAuthz(allow=True))
    pipeline.process(state, _serder(), attachments=[])
    assert issuer.calls == 0 and deliverer.delivered == []


def test_unknown_route_is_silent():
    issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
    state = _state(_svc_with_acdc_command(), ledger, issuer, FakeResolver(),
                   deliverer, FakeVerifier(), FakeAuthz(allow=True))
    pipeline.process(state, _serder(route="/svc/cmd/nope"), attachments=[])
    assert issuer.calls == 0 and deliverer.delivered == []


def test_reject_and_none_are_silent():
    for kind in ("reject", "none"):
        svc = ServiceAid(alias="svc")

        @svc.command(route="/svc/cmd/q")
        def q(req, _kind=kind):
            return Reply.reject(reason="no") if _kind == "reject" else Reply.none()

        issuer, deliverer, ledger = FakeIssuer(), FakeDeliverer(), FakeLedger()
        state = _state(svc, ledger, issuer, FakeResolver(), deliverer,
                       FakeVerifier(), FakeAuthz(allow=True))
        pipeline.process(state, _serder(route="/svc/cmd/q"), attachments=[])
        assert issuer.calls == 0 and deliverer.delivered == []
        assert ledger.store == {}
