import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from serviceaid.config import Config, load_bran

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("SERVICEAID_ALIAS", "rating")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "keri-core")
    monkeypatch.setenv("SERVICEAID_KEEPER_TABLE", "rating-ks")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "BWit1,BWit2")
    monkeypatch.setenv("SERVICEAID_TOAD", "2")
    monkeypatch.setenv("SERVICEAID_HANDLER", "rating_handler")
    monkeypatch.setenv("SERVICEAID_BRAN_SECRET", "rating/bran")
    monkeypatch.setenv("SERVICEAID_ALLOWLIST", "Ealice,Ebob")
    monkeypatch.setenv("SERVICEAID_REQUIRED_SCHEMA", "ESchemaReq")
    cfg = Config.from_env()
    assert cfg.alias == "rating"
    assert cfg.core_table == "keri-core"
    assert cfg.keeper_table == "rating-ks"
    assert cfg.witnesses == ["BWit1", "BWit2"]
    assert cfg.toad == 2
    assert cfg.handler_module == "rating_handler"
    assert cfg.bran_secret == "rating/bran"
    assert cfg.allowlist == ["Ealice", "Ebob"]
    assert cfg.required_schema == "ESchemaReq"
    assert cfg.kel_namespace == "rating:kel"
    assert cfg.tel_namespace == "rating:tel"


def test_config_toad_defaults_to_witness_count(monkeypatch):
    for k in ("SERVICEAID_TOAD",):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SERVICEAID_ALIAS", "r")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "c")
    monkeypatch.setenv("SERVICEAID_KEEPER_TABLE", "k")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "B1,B2,B3")
    monkeypatch.setenv("SERVICEAID_HANDLER", "h")
    cfg = Config.from_env()
    assert cfg.toad == 3


@needs_moto
def test_load_bran_from_secrets_manager():
    import boto3
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="a" * 21)
        assert load_bran("rating/bran", region="us-east-1") == "a" * 21
