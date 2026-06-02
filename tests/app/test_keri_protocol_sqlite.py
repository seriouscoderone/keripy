# -*- encoding: utf-8 -*-
"""Integration test: full KERI protocol patterns on SQLiteDBer."""

import pytest

try:
    from keri.db.sqlitedbing import SQLiteDBer
    HAS_SQLITE = True
except ImportError:
    HAS_SQLITE = False


BASER_STORES = [s + "." for s in [
    "evts", "fels", "kels", "dtss", "aess", "sigs", "wigs",
    "rcts", "ures", "vrcs", "vres", "pses", "pwes", "pdes",
    "udes", "uwes", "ooes", "dels", "ldes", "qnfs", "fons",
    "migs", "vers", "esrs", "misfits", "delegables", "states",
    "wits", "habs", "names", "sdts", "ssgs", "scgs", "rpys",
    "rpes", "eans", "lans", "ends", "locs", "obvs", "tops",
    "gpse", "gdee", "gpwe", "cgms", "epse", "epsd", "exns",
    "erpy", "esigs", "ecigs", "epath", "essrs", "chas", "reps",
    "wkas", "kdts", "ksns", "knas", "wwas", "oobis", "eoobi",
    "coobi", "roobi", "woobi", "moobi", "mfa", "rmfa", "schema",
    "cfld", "hbys", "cons", "ccigs", "imgs", "ifld", "sids",
    "icigs", "iimgs", "dpwe", "dune",
]]


@pytest.fixture
def sqlite_baser(tmp_path):
    if not HAS_SQLITE:
        pytest.skip("sqlitedbing not available")
    path = str(tmp_path / "test_protocol.sqlite")
    db = SQLiteDBer.open(name="test", stores=BASER_STORES, path=path)
    yield db
    db.close(clear=True)


class TestSQLiteProtocol:
    def test_sqlite_baser_opens_all_stores(self, sqlite_baser):
        for store_name in BASER_STORES:
            sdb = sqlite_baser.env.open_db(store_name.encode())
            assert sdb.opened is True

    def test_roundtrip_event_storage(self, sqlite_baser):
        sdb = sqlite_baser.env.open_db(b"evts.")
        key = b"DKxy2sgzfplyr-tgwIxS19f2OchFHtLwPWD3v4oYimBIs.00000000000000000000000000000000"
        val = b'{"v":"KERI10JSON000000_","t":"icp","d":"EKxy..."}'
        assert sqlite_baser.setVal(sdb, key, val) is True
        assert sqlite_baser.getVal(sdb, key) == val

    def test_ordinal_kel_storage(self, sqlite_baser):
        sdb = sqlite_baser.env.open_db(b"kels.")
        prefix = b"DKxy2sgzfplyr"
        sqlite_baser.putOnVal(sdb, prefix, on=0, val=b"inception_event")
        sqlite_baser.putOnVal(sdb, prefix, on=1, val=b"rotation_event")
        sqlite_baser.putOnVal(sdb, prefix, on=2, val=b"interaction_event")
        assert sqlite_baser.getOnVal(sdb, prefix, on=0) == b"inception_event"
        assert sqlite_baser.getOnVal(sdb, prefix, on=1) == b"rotation_event"
        assert sqlite_baser.getOnVal(sdb, prefix, on=2) == b"interaction_event"
        assert sqlite_baser.cntOnAll(sdb, prefix) == 3

    def test_ioset_signature_storage(self, sqlite_baser):
        sdb = sqlite_baser.env.open_db(b"sigs.")
        key = b"DKxy2sgzfplyr.00000000000000000000000000000000"
        sigs = [b"sig_from_key_0", b"sig_from_key_1", b"sig_from_key_2"]
        sqlite_baser.putIoSetVals(sdb, key, sigs)
        stored = [v for k, v in sqlite_baser.getIoSetItemIter(sdb, key)]
        assert stored == sigs
        assert sqlite_baser.cntIoSet(sdb, key) == 3

    def test_data_persists_across_close_reopen(self, tmp_path):
        if not HAS_SQLITE:
            pytest.skip("sqlitedbing not available")
        path = str(tmp_path / "persist.sqlite")
        db1 = SQLiteDBer.open(name="persist", stores=["test."], path=path)
        sdb1 = db1.env.open_db(b"test.")
        db1.setVal(sdb1, b"key1", b"val1")
        db1.close()
        db2 = SQLiteDBer.open(name="persist", stores=["test."], path=path)
        sdb2 = db2.env.open_db(b"test.")
        assert db2.getVal(sdb2, b"key1") == b"val1"
        db2.close()
