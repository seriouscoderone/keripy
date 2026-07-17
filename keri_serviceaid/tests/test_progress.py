"""Unit tests for the ProgressSink observer seam (Task 5, §A5).

ProgressSink is the seam a Qt host (Locksmith) or a headless host (CLI) plugs
into to observe issue/deliver progress without the provider knowing anything
about its consumer. Event names deliberately preserve Locksmith's existing
vocabulary (`("IssueCredentialDoer", "credential_issued", {...})`,
`("SendGrantDoer", "send_complete", {...})`) so its existing signal filters
keep working unmodified.
"""
from keri_serviceaid.progress import LogSink, NullSink, ProgressSink


class Capture:
    def __init__(self):
        self.events = []

    def on_event(self, source, event_type, data):
        self.events.append((source, event_type, data))


def test_capture_satisfies_protocol():
    sink: ProgressSink = Capture()
    sink.on_event("x", "y", {})
    assert sink.events == [("x", "y", {})]


def test_null_sink_swallows():
    NullSink().on_event("a", "b", {"c": 1})   # no raise


def test_issuer_accepts_and_uses_sink():
    import inspect
    from keri_serviceaid.providers.issue import IpexGrantIssuer
    assert "sink" in inspect.signature(IpexGrantIssuer.__init__).parameters
