"""Allowlist authorizer: empty ⇒ any sender; non-empty ⇒ membership."""
from keri_serviceaid import Allowlist, Request


def _req(sender):
    return Request(sender=sender, route="/x", payload={})


def test_empty_allowlist_allows_any():
    allow, reason = Allowlist([]).authorize(_req("EAnyone"))
    assert allow is True and reason == ""


def test_allowlist_allows_member():
    allow, reason = Allowlist(["EReq1", "EReq2"]).authorize(_req("EReq2"))
    assert allow is True and reason == ""


def test_allowlist_denies_nonmember():
    allow, reason = Allowlist(["EReq1"]).authorize(_req("EReq2"))
    assert allow is False and "not in allowlist" in reason
