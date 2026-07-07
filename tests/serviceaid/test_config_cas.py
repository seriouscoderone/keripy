from keri_serviceaid.config import Config


def test_pub_namespace_derived_from_alias():
    cfg = Config(alias="schema-publisher", core_table="keri-core",
                 keeper_secret="keri/schema-publisher/keeper", region="us-east-1")
    assert cfg.pub_namespace == "schema-publisher:pub"


def test_cas_bucket_from_env(monkeypatch):
    monkeypatch.setenv("SERVICEAID_CAS_BUCKET", "my-schema-cas")
    monkeypatch.setenv("SERVICEAID_ALIAS", "schema-publisher")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "keri-core")
    monkeypatch.setenv("SERVICEAID_KEEPER_SECRET", "keri/schema-publisher/keeper")
    monkeypatch.setenv("SERVICEAID_REGION", "us-east-1")
    cfg = Config.from_env()
    assert cfg.cas_bucket == "my-schema-cas"
