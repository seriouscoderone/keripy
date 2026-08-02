"""Test the revoke branch of pipeline.process (§6.1 wiring).

Fakes every provider so no keripy stack is needed. Verifies:
- issuer.revoke() called exactly once; issuer.issue() NOT called.
- deliverer.deliver() receives the notice returned by issuer.revoke().
- idempotency.record() key is serder.said (the inbound exn SAID — native dedup key).
- Record happens BEFORE delivery (exactly-once guarantee).
"""
from __future__ import annotations

from types import SimpleNamespace

from keri_serviceaid import pipeline
from keri_serviceaid.contract import Reply, ServiceAid
from keri_serviceaid.providers.verify import KeyState


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeVerifier:
    # Signature mirrors the Verifier Protocol (providers/verify.py): the
    # pipeline passes the command's floor as keyword-only `min_tier`.
    def verify(self, sender, ims, hby, *, min_tier=None):
        return KeyState(pre=sender, tier=min_tier or "receipts")


class _FakeAuthz:
    def authorize(self, req):
        return True, ""


class _FakeIdempotency:
    def __init__(self):
        self._seen: dict = {}
        self.recorded: list[tuple] = []
        self._delivery_count_at_record: list[int] = []
        self._deliver_ref = None   # set by _FakeDeliverer

    def seen(self, said: str):
        return self._seen.get(said)

    def record(self, said: str, notice: bytes):
        # Capture delivery count BEFORE record returns — proves BEFORE delivery.
        count = len(self._deliver_ref.calls) if self._deliver_ref else 0
        self._delivery_count_at_record.append(count)
        self.recorded.append((said, notice))


class _FakeIssuer:
    def __init__(self):
        self.revoke_calls: list = []
        self.issue_calls: list = []

    def issue(self, reply, ctx):
        self.issue_calls.append(reply)
        return b"GRANT"

    def revoke(self, reply, ctx):
        self.revoke_calls.append(reply)
        return b"NOTICE"


class _FakeDeliverer:
    def __init__(self):
        self.calls: list[tuple] = []

    def deliver(self, notice, endpoint, ctx):
        self.calls.append((notice, endpoint, ctx))


class _FakeResolver:
    def resolve(self, sender, hby):
        return SimpleNamespace(eid="ERecipient")


def _build_state(fn):
    """Build a minimal RuntimeState-like namespace with a single command."""
    svc = ServiceAid(alias="testsaid")

    @svc.command(route="/revoke", issues="")
    def _cmd(req):
        return fn(req)

    fake_idempotency = _FakeIdempotency()
    fake_issuer = _FakeIssuer()
    fake_deliverer = _FakeDeliverer()
    fake_idempotency._deliver_ref = fake_deliverer

    svc.verifier = _FakeVerifier()
    svc.authz = _FakeAuthz()
    svc.idempotency = fake_idempotency
    svc.issuer = fake_issuer
    svc.deliverer = fake_deliverer
    svc.resolver = _FakeResolver()

    state = SimpleNamespace(
        svc=svc,
        hby=None,
        hab=None,
        rgy=None,
        cfg=SimpleNamespace(alias="testsaid"),
    )
    return state, fake_idempotency, fake_issuer, fake_deliverer


def _serder(said: str, sender: str, route: str):
    return SimpleNamespace(
        ked={"i": sender, "r": route, "a": {}},
        said=said,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_pipeline_revoke_path():
    """Revoke reply routes through issuer.revoke, records on exn SAID, then delivers."""
    SAID = "ETestExnSaid"
    SENDER = "ETestSender"

    state, fake_idempotency, fake_issuer, fake_deliverer = _build_state(
        lambda req: Reply.revoke(recipient=SENDER, credential_said="ECredSAID")
    )
    serder = _serder(SAID, SENDER, "/revoke")

    pipeline.process(state, serder, attachments=[])

    # issuer.revoke called once, issuer.issue NOT called.
    assert len(fake_issuer.revoke_calls) == 1, "issuer.revoke must be called exactly once"
    assert len(fake_issuer.issue_calls) == 0, "issuer.issue must NOT be called on revoke"

    # deliverer received the notice returned by issuer.revoke.
    assert len(fake_deliverer.calls) == 1
    notice, endpoint, ctx = fake_deliverer.calls[0]
    assert notice == b"NOTICE", f"deliverer must receive issuer.revoke return value; got {notice!r}"

    # Idempotency key is the inbound exn SAID.
    assert len(fake_idempotency.recorded) == 1
    recorded_said, recorded_notice = fake_idempotency.recorded[0]
    assert recorded_said == SAID, (
        f"idempotency.record must use serder.said={SAID!r}, got {recorded_said!r}"
    )

    # Record happens BEFORE delivery (exactly-once: record=0 deliveries at record time).
    assert fake_idempotency._delivery_count_at_record[0] == 0, (
        "idempotency.record must be called BEFORE deliverer.deliver"
    )


def test_pipeline_acdc_path_unchanged():
    """Sanity: the acdc branch still routes through issuer.issue (not revoke)."""
    SAID = "EAcdcExnSaid"
    SENDER = "EAcdcSender"

    state, fake_idempotency, fake_issuer, fake_deliverer = _build_state(
        lambda req: Reply.acdc(recipient=SENDER, attributes={"x": 1})
    )
    # The acdc branch stamps schema_said from cmd.issues — set it on the command.
    state.svc._commands["/revoke"].issues = "ESchemaSaid"
    serder = _serder(SAID, SENDER, "/revoke")

    pipeline.process(state, serder, attachments=[])

    assert len(fake_issuer.issue_calls) == 1, "acdc path must call issuer.issue"
    assert len(fake_issuer.revoke_calls) == 0, "acdc path must NOT call issuer.revoke"
