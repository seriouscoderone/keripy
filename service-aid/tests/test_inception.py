import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")

# Defensively unset any stale SERVICEAID_* vars that might be present in the
# outer environment and would confuse Config.from_env() or cause cross-test
# leakage. monkeypatch restores these automatically after each test.
_OPTIONAL_VARS = (
    "SERVICEAID_ALLOWLIST",
    "SERVICEAID_REQUIRED_SCHEMA",
    "SERVICEAID_TOAD",
    "SERVICEAID_REGION",
    "SERVICEAID_ENDPOINT_URL",
)


@needs_moto
def test_on_create_incepts_and_returns_pre(monkeypatch):
    import boto3
    from serviceaid import runtime
    from serviceaid.cdk import inception
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="q" * 21)
        runtime.reset()
        env = {"SERVICEAID_ALIAS": "rating", "SERVICEAID_CORE_TABLE": "keri-core",
               "SERVICEAID_KEEPER_TABLE": "rating-ks", "SERVICEAID_WITNESSES": "",
               "SERVICEAID_HANDLER": "", "SERVICEAID_BRAN_SECRET": "rating/bran"}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        for var in _OPTIONAL_VARS:
            monkeypatch.delenv(var, raising=False)

        event = {"RequestType": "Create"}
        result = inception.on_event(event, None)
        assert result["PhysicalResourceId"].startswith(("E", "D"))
        assert result["Data"]["ServiceAidPre"] == result["PhysicalResourceId"]


@needs_moto
def test_on_update_is_idempotent(monkeypatch):
    import boto3
    from serviceaid import runtime
    from serviceaid.cdk import inception
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="q" * 21)
        runtime.reset()
        env = {"SERVICEAID_ALIAS": "rating", "SERVICEAID_CORE_TABLE": "keri-core",
               "SERVICEAID_KEEPER_TABLE": "rating-ks", "SERVICEAID_WITNESSES": "",
               "SERVICEAID_HANDLER": "", "SERVICEAID_BRAN_SECRET": "rating/bran"}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        for var in _OPTIONAL_VARS:
            monkeypatch.delenv(var, raising=False)

        created = inception.on_event({"RequestType": "Create"}, None)
        pre = created["PhysicalResourceId"]

        # No runtime.reset() between calls — realistic warm-container path;
        # on_event resets internally before init().
        updated = inception.on_event(
            {"RequestType": "Update", "PhysicalResourceId": pre}, None)
        assert updated["PhysicalResourceId"] == pre
        assert updated["Data"]["ServiceAidPre"] == pre


def test_on_delete_is_noop():
    from serviceaid.cdk import inception
    event = {"RequestType": "Delete", "PhysicalResourceId": "Eexisting"}
    result = inception.on_event(event, None)
    assert result["PhysicalResourceId"] == "Eexisting"
