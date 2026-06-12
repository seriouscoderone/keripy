import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from serviceaid.config import Config

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("SERVICEAID_ALIAS", "rating")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "keri-core")
    monkeypatch.setenv("SERVICEAID_KEEPER_SECRET", "keri/rating/keeper")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "BWit1,BWit2")
    monkeypatch.setenv("SERVICEAID_TOAD", "2")
    monkeypatch.setenv("SERVICEAID_HANDLER", "rating_handler")
    monkeypatch.setenv("SERVICEAID_ALLOWLIST", "Ealice,Ebob")
    monkeypatch.setenv("SERVICEAID_REQUIRED_SCHEMA", "ESchemaReq")
    cfg = Config.from_env()
    assert cfg.alias == "rating"
    assert cfg.core_table == "keri-core"
    assert cfg.keeper_secret == "keri/rating/keeper"
    assert cfg.witnesses == ["BWit1", "BWit2"]
    assert cfg.toad == 2
    assert cfg.handler_module == "rating_handler"
    assert cfg.allowlist == ["Ealice", "Ebob"]
    assert cfg.required_schema == "ESchemaReq"
    assert cfg.kel_namespace == "rating:kel"
    assert cfg.tel_namespace == "rating:tel"


def test_keeper_secret_defaults_from_alias(monkeypatch):
    """When SERVICEAID_KEEPER_SECRET is unset, it derives by convention from
    the alias: keri/<alias>/keeper."""
    monkeypatch.delenv("SERVICEAID_KEEPER_SECRET", raising=False)
    monkeypatch.setenv("SERVICEAID_ALIAS", "rating")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "keri-core")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "")
    monkeypatch.setenv("SERVICEAID_HANDLER", "")
    cfg = Config.from_env()
    assert cfg.keeper_secret == "keri/rating/keeper"


def test_secret_endpoint_url_defaults_none_and_reads_env(monkeypatch):
    """secret_endpoint_url is None in production (real AWS) and only set for
    local dev to split the keeper SecretStore off the DynamoDB endpoint."""
    monkeypatch.setenv("SERVICEAID_ALIAS", "rating")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "keri-core")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "")
    monkeypatch.setenv("SERVICEAID_HANDLER", "")
    # Default: unset => None (production: both endpoints None).
    monkeypatch.delenv("SERVICEAID_SECRET_ENDPOINT_URL", raising=False)
    assert Config.from_env().secret_endpoint_url is None
    # Reads the dedicated env var when present.
    monkeypatch.setenv("SERVICEAID_SECRET_ENDPOINT_URL", "https://sm.example")
    assert Config.from_env().secret_endpoint_url == "https://sm.example"


def test_config_toad_defaults_to_witness_count(monkeypatch):
    for k in ("SERVICEAID_TOAD",):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SERVICEAID_ALIAS", "r")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "c")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "B1,B2,B3")
    monkeypatch.setenv("SERVICEAID_HANDLER", "h")
    cfg = Config.from_env()
    assert cfg.toad == 3
