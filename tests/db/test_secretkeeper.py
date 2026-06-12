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


@needs_moto
def test_secretkeeper_empty_key_raises():
    from moto import mock_aws
    with mock_aws():
        store = SecretStore(region="us-east-1")
        ks = SecretKeeper(store=store, secret_name="keri/svc/keeper",
                          salt="0Asalt", bran="b" * 21)
        sub = ks.env.open_db(b"gbls.")
        with pytest.raises(KeyError):
            ks.setVal(sub, b"", b"v")
        with pytest.raises(KeyError):
            ks.getVal(sub, b"")
        with pytest.raises(KeyError):
            ks.putVal(sub, b"", b"v")


@needs_moto
def test_secretkeeper_deferflush_single_write():
    from moto import mock_aws
    with mock_aws():
        store = SecretStore(region="us-east-1")
        ks = SecretKeeper(store=store, secret_name="keri/svc/keeper",
                          salt="0Asalt", bran="b" * 21)
        sub = ks.env.open_db(b"gbls.")

        calls = {"n": 0}
        orig_put = store.put

        def counting_put(name, value):
            calls["n"] += 1
            return orig_put(name, value)

        store.put = counting_put

        # Several mutations inside one deferflush block => exactly one write.
        with ks.deferflush():
            ks.setVal(sub, b"k1", b"v1")
            ks.setVal(sub, b"k2", b"v2")
            ks.setVal(sub, b"k3", b"v3")
            assert calls["n"] == 0          # nothing written mid-ceremony
        assert calls["n"] == 1              # one atomic flush on exit

        # Secret reflects all values after the block.
        import json
        doc = json.loads(store.get("keri/svc/keeper"))
        data = loadKeeper(doc["keeper"])
        assert data["gbls."] == {b"k1".hex(): b"v1",
                                 b"k2".hex(): b"v2",
                                 b"k3".hex(): b"v3"}

        # Nested deferflush flushes only at the outermost exit.
        calls["n"] = 0
        with ks.deferflush():
            ks.setVal(sub, b"k4", b"v4")
            with ks.deferflush():
                ks.setVal(sub, b"k5", b"v5")
            assert calls["n"] == 0          # inner exit did not flush
        assert calls["n"] == 1              # only outermost exit flushed


@needs_moto
def test_secretkeeper_deferflush_no_write_on_exception():
    from moto import mock_aws
    with mock_aws():
        store = SecretStore(region="us-east-1")
        ks = SecretKeeper(store=store, secret_name="keri/svc/keeper",
                          salt="0Asalt", bran="b" * 21)
        sub = ks.env.open_db(b"gbls.")

        calls = {"n": 0}
        orig_put = store.put

        def counting_put(name, value):
            calls["n"] += 1
            return orig_put(name, value)

        store.put = counting_put

        # A ceremony that writes some entries then raises mid-block must NOT
        # persist the partial in-memory keeper.
        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            with ks.deferflush():
                ks.setVal(sub, b"pres", b"prefixer")
                ks.setVal(sub, b"prms", b"preprm")
                raise Boom()                     # crash before pris written

        assert calls["n"] == 0                   # zero writes: no partial flush
        assert ks._defer_depth == 0              # depth restored after abort
        assert store.get("keri/svc/keeper") is None  # nothing ever persisted


@needs_moto
def test_habery_incepts_and_signs_over_secretkeeper():
    """Keystone: a real Habery incepts an AID with ks=SecretKeeper, the keeper
    persists to a (moto) Secrets Manager secret, a fresh cold start reloads it,
    and signing produces a verifiable signature.

    The private key material lives ONLY in the secret-backed keeper. The
    cold-start Habery is given a genuinely fresh ``SecretKeeper`` reloaded from
    the secret; the public-side KEL (Baser) is the only thing shared across the
    cold start, mirroring production where the public DB is durable and the
    keeper round-trips through the KMS-encrypted secret. ``hab3.sign`` therefore
    can only succeed if the private keys survived the secret round-trip, and the
    verify proves they are the right keys for the reloaded public KEL.
    """
    from moto import mock_aws
    from keri.app.habbing import Habery
    from keri.app.lambding import setup_keeper
    from keri.core.signing import Salter
    from keri.db.basing import Baser
    import json
    with mock_aws():
        store = SecretStore(region="us-east-1")
        salt = Salter(raw=b'0123456789abcdef').qb64
        bran = "b" * 21
        # provision the keeper secret (as the inception CR will, Task 6)
        store.put("keri/svc/keeper", json.dumps(
            {"v": 1, "salt": salt, "bran": bran, "keeper": None}))

        # Durable public-side DB shared across the cold start (private keys are
        # NOT stored here — they live only in the secret-backed keeper).
        db = Baser(name="svc", temp=True, reopen=True)

        ks = SecretKeeper.open(store=store, secret_name="keri/svc/keeper")
        setup_keeper(ks)
        hby = Habery(name="svc", temp=True, ks=ks, db=db, salt=salt, bran=bran)
        with ks.deferflush():                       # single atomic keeper write
            hab = hby.makeHab(name="svc", transferable=True)
        pre = hab.pre
        assert ks.bran == bran

        # keeper persisted to the secret: a fresh SecretKeeper over the same
        # secret carries the private key material
        ks2 = SecretKeeper.open(store=store, secret_name="keri/svc/keeper")
        assert ks2.cntAll(ks2.env.open_db(b"pris.")) >= 1   # LOAD-BEARING

        # release the first keeper only; keep the public DB open for cold start
        ks.close()

        # cold start over the persisted keeper: a genuinely fresh SecretKeeper
        # reloaded from the secret supplies the private keys for signing.
        ks3 = SecretKeeper.open(store=store, secret_name="keri/svc/keeper")
        setup_keeper(ks3)
        hby3 = Habery(name="svc", temp=True, ks=ks3, db=db, salt=salt, bran=bran)
        hab3 = hby3.habByName("svc")
        assert hab3 is not None and hab3.pre == pre   # public KEL reloaded
        sigs = hab3.sign(b"hello world")              # keys from reloaded keeper
        assert sigs and hab3.kever.verfers[0].verify(sigs[0].raw, b"hello world")
        hby3.close(clear=True)
