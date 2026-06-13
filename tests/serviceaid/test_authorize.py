from keri_cdk.handlers.serviceaid.authorize import Policy, authorize
from keri_cdk.handlers.serviceaid.contract import Request


def _req(sender="Ecaller", creds=None):
    return Request(sender=sender, payload={}, credentials=creds or [],
                   message_said="m", payload_said="p", route="/r")


def test_no_policy_allows_all():
    ok, reason = authorize(_req(), Policy())
    assert ok and reason == ""


def test_allowlist_permits_listed_sender():
    ok, _ = authorize(_req(sender="Eok"), Policy(allowlist=["Eok"]))
    assert ok


def test_allowlist_rejects_unlisted_sender():
    ok, reason = authorize(_req(sender="Ebad"), Policy(allowlist=["Eok"]))
    assert not ok and "allowlist" in reason


def test_required_credential_present():
    creds = [{"schema": "ESchemaX", "issuer": "Eiss"}]
    ok, _ = authorize(_req(creds=creds), Policy(required_schema="ESchemaX"))
    assert ok


def test_required_credential_missing():
    ok, reason = authorize(_req(creds=[]), Policy(required_schema="ESchemaX"))
    assert not ok and "credential" in reason


def test_malformed_credential_entries_deny_not_crash():
    ok, reason = authorize(_req(creds=[None, "junk", {"noschema": 1}]),
                           Policy(required_schema="ESchemaX"))
    assert not ok and "credential" in reason


def test_both_policies_must_pass():
    pol = Policy(allowlist=["Eok"], required_schema="ESchemaX")
    # passes allowlist, lacks credential -> deny
    ok, reason = authorize(_req(sender="Eok", creds=[]), pol)
    assert not ok and "credential" in reason
    # has credential, fails allowlist -> deny
    ok, reason = authorize(_req(sender="Ebad",
                                creds=[{"schema": "ESchemaX"}]), pol)
    assert not ok and "allowlist" in reason
    # both satisfied -> allow
    ok, reason = authorize(_req(sender="Eok",
                                creds=[{"schema": "ESchemaX"}]), pol)
    assert ok and reason == ""
