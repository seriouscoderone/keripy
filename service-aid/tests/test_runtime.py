import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from serviceaid.config import Config
from serviceaid import runtime

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def _cfg(**over):
    base = dict(alias="rating", core_table="keri-core", keeper_table="rating-ks",
                witnesses=[], toad=0, handler_module="", bran_secret="rating/bran",
                region="us-east-1", endpoint_url=None)
    base.update(over)
    return Config(**base)


@needs_moto
def test_init_incepts_transferable_aid_with_encrypted_keeper(monkeypatch):
    import boto3
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="x" * 21)

        runtime.reset()  # clear warm singletons between tests
        state = runtime.init(_cfg())

        # Transferable AID created.
        assert state.hab.pre.startswith("E") or state.hab.pre.startswith("D")
        assert state.hab.kever.transferable is True
        # Keeper encryption engaged (aeid set => private keys are ciphertext at rest).
        assert state.hby.ks.gbls.get("aeid") is not None
        # A credential registry exists for this service.
        assert state.rgy.registryByName("rating") is not None


@needs_moto
def test_init_is_warm_idempotent(monkeypatch):
    import boto3
    with mock_aws():
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="rating/bran", SecretString="x" * 21)
        runtime.reset()
        s1 = runtime.init(_cfg())
        s2 = runtime.init(_cfg())          # warm: returns the same singleton
        assert s1 is s2
        assert s1.hab.pre == s2.hab.pre
