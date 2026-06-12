# -*- encoding: utf-8 -*-
"""Tests for the secret-backed keeper (SecretStore + SecretKeeper)."""
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")

from keri.db.secretkeeper import SecretStore, dumpKeeper, loadKeeper


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
