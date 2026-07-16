from types import SimpleNamespace
from keri_serviceaid import ServiceAid, Reply, KeyState, VerificationError
from keri_serviceaid import pipeline


class CallLog:
    def __init__(self): self.events = []


class RecordingVerifier:
    """Records the min_tier the pipeline computed, and can raise."""
    def __init__(self, log, raise_=False):
        self.log, self.raise_ = log, raise_
    def verify(self, sender, ims, hby, *, min_tier=None):
        self.log.events.append(("verify", min_tier))
        if self.raise_:
            raise VerificationError("tier unmet")
        return KeyState(pre=sender, tier=min_tier or "signed")


class FakeAuthz:
    def __init__(self, log): self.log = log
    def authorize(self, req):
        self.log.events.append(("authorize", req.route)); return True, ""


def _serder(route, sender="EReq", said="ESaid"):
    return SimpleNamespace(ked={"i": sender, "r": route, "a": {}}, said=said, raw=b"")


def _svc_with_command(min_tier):
    svc = ServiceAid(alias="svc")
    @svc.command(route="/rate", min_assurance_tier=min_tier)
    def _rate(req): return Reply.none()  # kind unexamined by these drop/authorize assertions
    return svc


def _state(svc):
    return SimpleNamespace(svc=svc, hby=object(), hab=object(), rgy=object(),
                           cfg=SimpleNamespace(alias="svc"))


def test_pipeline_passes_command_min_tier_to_verify():
    log = CallLog()
    svc = _svc_with_command("receipts")
    svc.verifier = RecordingVerifier(log)
    svc.authz = FakeAuthz(log)
    svc.idempotency = SimpleNamespace(seen=lambda said: None)
    pipeline.process(_state(svc), _serder("/rate"), attachments=b"")
    assert ("verify", "receipts") in log.events           # effective tier reached verify
    assert log.events.index(("verify", "receipts")) < \
           log.events.index(("authorize", "/rate"))       # verify before authorize


def test_pipeline_unknown_route_drops_before_verify():
    log = CallLog()
    svc = _svc_with_command("receipts")
    svc.verifier = RecordingVerifier(log)
    svc.authz = FakeAuthz(log)
    svc.idempotency = SimpleNamespace(seen=lambda said: None)
    pipeline.process(_state(svc), _serder("/unknown"), attachments=b"")
    assert log.events == []                                # dropped at lookup, verify never ran
