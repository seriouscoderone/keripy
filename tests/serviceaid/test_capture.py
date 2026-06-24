"""_CaptureHandler stashes verified exns for synchronous drain."""
from keri_serviceaid._capture import _CaptureHandler


class FakeSerder:
    pass


def test_capture_handle_then_drain_returns_and_clears():
    h = _CaptureHandler(resource="/rate")
    assert h.resource == "/rate"
    assert h.verify(FakeSerder()) is True

    s1, s2 = FakeSerder(), FakeSerder()
    h.handle(s1, attachments=[b"a"])
    h.handle(s2)
    drained = h.drain()
    assert [d[0] for d in drained] == [s1, s2]
    assert drained[0][1] == [b"a"]
    assert h.drain() == []        # buffer cleared after drain
