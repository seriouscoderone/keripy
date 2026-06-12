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
    "SERVICEAID_SECRET_ENDPOINT_URL",
)


def _set_env(monkeypatch):
    env = {"SERVICEAID_ALIAS": "rating", "SERVICEAID_CORE_TABLE": "keri-core",
           "SERVICEAID_KEEPER_SECRET": "keri/rating/keeper",
           "SERVICEAID_WITNESSES": "", "SERVICEAID_HANDLER": ""}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for var in _OPTIONAL_VARS:
        monkeypatch.delenv(var, raising=False)


def _read_keeper(name="keri/rating/keeper"):
    import boto3, json
    resp = boto3.client("secretsmanager", region_name="us-east-1"
                        ).get_secret_value(SecretId=name)
    return json.loads(resp["SecretString"])


@needs_moto
def test_on_create_get_or_creates_keeper_secret(monkeypatch):
    from serviceaid import runtime
    from serviceaid.cdk import inception
    with mock_aws():
        runtime.reset()
        _set_env(monkeypatch)

        # No pre-provisioned keeper secret: the CR itself must create it.
        result = inception.on_event({"RequestType": "Create"}, None)

        # The returned PhysicalResourceId is the AID prefix and is echoed in Data.
        pre = result["PhysicalResourceId"]
        assert isinstance(pre, str) and pre
        assert pre.startswith(("E", "D"))
        assert result["Data"]["ServiceAidPre"] == pre

        # The keeper secret was get-or-created with non-empty salt + bran, and
        # runtime.init() incepted + flushed the keeper blob so it is non-null.
        doc = _read_keeper()
        assert isinstance(doc["salt"], str) and doc["salt"]
        assert isinstance(doc["bran"], str) and len(doc["bran"]) >= 21
        assert "keeper" in doc
        assert doc["keeper"] is not None  # incept flushed the keystore blob


@needs_moto
def test_on_create_is_idempotent_does_not_overwrite_salt(monkeypatch):
    from serviceaid import runtime
    from serviceaid.cdk import inception
    with mock_aws():
        runtime.reset()
        _set_env(monkeypatch)

        created = inception.on_event({"RequestType": "Create"}, None)
        pre = created["PhysicalResourceId"]
        salt1 = _read_keeper()["salt"]
        bran1 = _read_keeper()["bran"]

        # No runtime.reset() between calls — realistic warm-container path;
        # on_event resets internally before init(). get_or_create must return
        # the EXISTING secret, never overwrite the salt/bran.
        updated = inception.on_event(
            {"RequestType": "Update", "PhysicalResourceId": pre}, None)
        assert updated["PhysicalResourceId"] == pre
        assert updated["Data"]["ServiceAidPre"] == pre

        doc2 = _read_keeper()
        assert doc2["salt"] == salt1       # salt unchanged across two calls
        assert doc2["bran"] == bran1       # bran unchanged across two calls


def test_on_delete_is_noop():
    from serviceaid.cdk import inception
    event = {"RequestType": "Delete", "PhysicalResourceId": "Eexisting"}
    result = inception.on_event(event, None)
    assert result["PhysicalResourceId"] == "Eexisting"
