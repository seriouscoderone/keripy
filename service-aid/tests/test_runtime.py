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
    base = dict(alias="rating", core_table="keri-core",
                keeper_secret="keri/rating/keeper",
                witnesses=[], toad=0, handler_module="",
                region="us-east-1", endpoint_url=None)
    base.update(over)
    return Config(**base)


def _provision_keeper_secret(name="keri/rating/keeper", bran="x" * 21):
    """Create the keeper secret the inception CR (Task 6) will provision: one
    secret holding {salt, bran, keeper-blob}. salt/bran drive aeid encryption."""
    import boto3, json
    from keri.core.signing import Salter
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(
        Name=name,
        SecretString=json.dumps({"v": 1,
                                 "salt": Salter(raw=b'0123456789abcdef').qb64,
                                 "bran": bran, "keeper": None}))


@needs_moto
def test_init_incepts_transferable_aid_with_encrypted_keeper(monkeypatch):
    with mock_aws():
        _provision_keeper_secret()

        runtime.reset()  # clear warm singletons between tests
        state = runtime.init(_cfg())

        # Transferable AID created.
        assert state.hab.pre.startswith("E") or state.hab.pre.startswith("D")
        assert state.hab.kever.transferable is True
        # Keeper encryption engaged: the bran from the secret drives aeid, so
        # private keys are ciphertext at rest.
        assert state.hby.ks.bran == "x" * 21
        assert state.hby.ks.gbls.get("aeid") is not None
        # A credential registry exists for this service.
        assert state.rgy.registryByName("rating") is not None


def test_capture_handler_drain_clears():
    from serviceaid.runtime import _CaptureHandler
    h = _CaptureHandler(resource="/r")
    h.handle(serder="s1")
    h.handle(serder="s2", attachments=["a"])
    out = h.drain()
    assert [s for s, _ in out] == ["s1", "s2"]
    assert h.drain() == []


@needs_moto
def test_init_is_warm_idempotent(monkeypatch):
    with mock_aws():
        _provision_keeper_secret()
        runtime.reset()
        s1 = runtime.init(_cfg())
        s2 = runtime.init(_cfg())          # warm: returns the same singleton
        assert s1 is s2
        assert s1.hab.pre == s2.hab.pre
