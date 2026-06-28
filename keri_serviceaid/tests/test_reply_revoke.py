"""Test Reply.revoke classmethod (§6.1 revoke kind)."""
from keri_serviceaid.contract import Reply


def test_reply_revoke_shape():
    r = Reply.revoke(recipient="EHolder", credential_said="ECredSAID", reason="cause")
    assert r.kind == "revoke"
    assert r.recipient == "EHolder"
    assert r.attributes["credential_said"] == "ECredSAID"
    assert r.reason == "cause"


def test_reply_revoke_reason_default():
    r = Reply.revoke(recipient="EHolder", credential_said="ECredSAID")
    assert r.reason == ""


def test_reply_revoke_does_not_break_existing():
    acdc = Reply.acdc(recipient="EFoo", attributes={"x": 1})
    assert acdc.kind == "acdc"
    none_ = Reply.none()
    assert none_.kind == "none"
    reject = Reply.reject(reason="bad")
    assert reject.kind == "reject"
    assert reject.reason == "bad"
