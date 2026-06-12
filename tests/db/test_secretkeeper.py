# -*- encoding: utf-8 -*-
"""Tests for the secret-backed keeper (SecretStore + SecretKeeper)."""
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")

from keri.db.secretkeeper import SecretStore, SecretKeeper, dumpKeeper, loadKeeper


@needs_moto
def test_secretstore_get_absent_returns_none():
    with mock_aws():
        store = SecretStore(region="us-east-1")
        assert store.get("keri/svc/keeper") is None


@needs_moto
def test_secretstore_put_then_get_roundtrip():
    with mock_aws():
        store = SecretStore(region="us-east-1")
        store.put("keri/svc/keeper", '{"v":1}')
        assert store.get("keri/svc/keeper") == '{"v":1}'


@needs_moto
def test_secretstore_get_or_create_is_idempotent():
    with mock_aws():
        store = SecretStore(region="us-east-1")
        created1, val1 = store.get_or_create("keri/svc/keeper", lambda: '{"v":1,"n":1}')
        created2, val2 = store.get_or_create("keri/svc/keeper", lambda: '{"v":1,"n":2}')
        assert created1 is True and val1 == '{"v":1,"n":1}'
        assert created2 is False and val2 == '{"v":1,"n":1}'   # existing wins


@needs_moto
def test_get_or_create_never_overwrites_existing():
    with mock_aws():
        store = SecretStore(region="us-east-1")
        store.put("keri/svc/keeper", '{"v":1,"n":1}')          # pre-existing
        created, val = store.get_or_create("keri/svc/keeper",
                                            lambda: '{"v":1,"n":2}')
        assert created is False and val == '{"v":1,"n":1}'     # existing wins
        assert store.get("keri/svc/keeper") == '{"v":1,"n":1}'  # unchanged on disk


@needs_moto
def test_put_updates_existing():
    with mock_aws():
        store = SecretStore(region="us-east-1")
        store.put("keri/svc/keeper", '{"v":1,"n":1}')
        store.put("keri/svc/keeper", '{"v":1,"n":2}')          # deliberate overwrite
        assert store.get("keri/svc/keeper") == '{"v":1,"n":2}'  # second value wins


def test_keeper_blob_roundtrip_bytes_values():
    data = {"gbls.": {"6165696400": b"aeid-value"},   # hex key -> bytes val
            "pris.": {"deadbeef": b"\x00\x01\x02ciphertext"}}
    blob = dumpKeeper(data)
    assert isinstance(blob, str)                 # base64 ascii, JSON-safe
    assert loadKeeper(blob) == data              # exact round-trip incl bytes


def test_keeper_blob_empty():
    assert loadKeeper(dumpKeeper({})) == {}


def test_keeper_blob_none_loads_empty():
    assert loadKeeper(None) == {}
    assert loadKeeper("") == {}


@needs_moto
def test_secretkeeper_kv_roundtrip_and_persist():
    from moto import mock_aws
    with mock_aws():
        store = SecretStore(region="us-east-1")
        ks = SecretKeeper(store=store, secret_name="keri/svc/keeper",
                          salt="0Asalt", bran="b" * 21)
        sub = ks.env.open_db(b"gbls.")
        assert ks.setVal(sub, b"aeid", b"Dpubkey") is True
        assert ks.getVal(sub, b"aeid") == b"Dpubkey"
        assert ks.putVal(sub, b"aeid", b"other") is False    # no overwrite
        import json
        doc = json.loads(store.get("keri/svc/keeper"))
        assert doc["salt"] == "0Asalt" and doc["bran"] == "b" * 21

        ks2 = SecretKeeper.open(store=store, secret_name="keri/svc/keeper")
        sub2 = ks2.env.open_db(b"gbls.")
        assert ks2.getVal(sub2, b"aeid") == b"Dpubkey"
        assert ks2.salt == "0Asalt" and ks2.bran == "b" * 21


@needs_moto
def test_secretkeeper_top_iter_and_rem():
    from moto import mock_aws
    with mock_aws():
        store = SecretStore(region="us-east-1")
        ks = SecretKeeper(store=store, secret_name="keri/svc/keeper",
                          salt="0Asalt", bran="b" * 21)
        sub = ks.env.open_db(b"pris.")
        ks.setVal(sub, b"k1", b"v1")
        ks.setVal(sub, b"k2", b"v2")
        assert dict(ks.getTopItemIter(sub)) == {b"k1": b"v1", b"k2": b"v2"}
        assert ks.remVal(sub, b"k1") is True
        assert ks.getVal(sub, b"k1") is None


def test_secretkeeper_unsupported_method_raises():
    ks = SecretKeeper(store=None, secret_name="x", salt=None, bran=None,
                      no_store=True)
    with pytest.raises(NotImplementedError):
        ks.getIoSetItemIter(None, b"k")
