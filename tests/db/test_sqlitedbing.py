# -*- encoding: utf-8 -*-
"""
tests.db.test_sqlitedbing module

Tests for SQLite-backed SQLiteDBer: schema, lifecycle, key utilities.
"""

import pytest

try:
    from keri.db.sqlitedbing import (
        SQLiteDBer,
        SQLiteSubDb,
        SQLiteEnv,
        openSQLite,
        onKey,
        splitKey,
        splitOnKey,
        suffix,
        unsuffix,
        MaxON,
    )
    HAS_SQLITEDBING = True
except ImportError:
    HAS_SQLITEDBING = False

needs_sqlitedbing = pytest.mark.skipif(
    not HAS_SQLITEDBING, reason="requires sqlitedbing"
)

STORES = ["evts.", "fels.", "kels.", "sigs.", "test."]


@pytest.fixture
def dber(tmp_path):
    """Provides a SQLiteDBer instance backed by a temp SQLite file."""
    if not HAS_SQLITEDBING:
        pytest.skip("sqlitedbing not available")
    db = SQLiteDBer.open(
        name="test",
        stores=STORES,
        path=str(tmp_path / "test.sqlite"),
    )
    yield db
    db.close(clear=True)


@needs_sqlitedbing
class TestKeyUtilities:
    """Test key composition utility functions."""

    def test_onKey(self):
        result = onKey(b"pre", 42)
        assert result == b"pre.%032x" % 42

    def test_splitOnKey(self):
        key = onKey(b"pre", 42)
        top, on = splitOnKey(key)
        assert top == b"pre"
        assert on == 42

    def test_suffix_unsuffix(self):
        key = b"mykey"
        iokey = suffix(key, 5)
        base, ion = unsuffix(iokey)
        assert base == key
        assert ion == 5

    def test_MaxON(self):
        assert MaxON == int("f" * 32, 16)


@needs_sqlitedbing
class TestSQLiteDBerLifecycle:
    """Test open/close/version lifecycle."""

    def test_open_creates_database(self, dber):
        assert dber.opened is True
        assert dber.name == "test"
        assert "evts." in dber.stores

    def test_open_with_clear(self, tmp_path):
        db_path = str(tmp_path / "cleartest.sqlite")
        db = SQLiteDBer.open(name="cleartest", stores=["core."], path=db_path)
        sdb = db.env.open_db(b"core.")
        db.setVal(sdb, b"key1", b"val1")
        db.close()

        db2 = SQLiteDBer.open(name="cleartest", stores=["core."],
                              path=db_path, clear=True)
        sdb2 = db2.env.open_db(b"core.")
        assert db2.getVal(sdb2, b"key1") is None
        db2.close()

    def test_version_get_set(self, dber):
        assert dber.version is None
        dber.version = "1.0.0"
        assert dber.version == "1.0.0"

    def test_close_clears(self, tmp_path):
        db_path = str(tmp_path / "closetest.sqlite")
        db = SQLiteDBer.open(name="closetest", stores=["core."], path=db_path)
        db.close(clear=True)
        assert db.opened is False

    def test_env_open_db(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert isinstance(sdb, SQLiteSubDb)
        assert sdb.opened is True

    def test_env_open_db_unknown_raises(self, dber):
        with pytest.raises(KeyError):
            dber.env.open_db(b"nonexistent.")

    def test_env_open_db_dupsort(self, dber):
        sdb = dber.env.open_db(b"test.", dupsort=True)
        assert sdb.flags() == {"dupsort": True}

    def test_flush_noop(self, dber):
        assert dber.flush() == 0


@needs_sqlitedbing
class TestContextManager:
    """Test openSQLite context manager."""

    def test_open_and_use(self, tmp_path):
        db_path = str(tmp_path / "ctx.sqlite")
        with openSQLite(name="ctx", stores=["test."],
                        path=db_path) as db:
            sdb = db.env.open_db(b"test.")
            db.setVal(sdb, b"key1", b"val1")
            assert db.getVal(sdb, b"key1") == b"val1"

    def test_temp_clears_on_exit(self, tmp_path):
        db_path = str(tmp_path / "temp.sqlite")
        with openSQLite(name="temp", stores=["test."],
                        path=db_path, temp=True) as db:
            sdb = db.env.open_db(b"test.")
            db.setVal(sdb, b"key1", b"val1")
        # After exit, stores should be cleared
        assert db.stores == []


@needs_sqlitedbing
class TestSingleValueCRUD:
    """Single key-value operations."""

    def test_putVal_insert(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putVal(sdb, b"key1", b"val1") is True
        assert dber.getVal(sdb, b"key1") == b"val1"

    def test_putVal_no_overwrite(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putVal(sdb, b"key1", b"val1")
        assert dber.putVal(sdb, b"key1", b"val2") is False
        assert dber.getVal(sdb, b"key1") == b"val1"

    def test_setVal_insert(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.setVal(sdb, b"key1", b"val1") is True
        assert dber.getVal(sdb, b"key1") == b"val1"

    def test_setVal_overwrite(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"key1", b"val1")
        assert dber.setVal(sdb, b"key1", b"val2") is True
        assert dber.getVal(sdb, b"key1") == b"val2"

    def test_getVal_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getVal(sdb, b"nonexistent") is None

    def test_remVal_exists(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"key1", b"val1")
        assert dber.remVal(sdb, b"key1") is True
        assert dber.getVal(sdb, b"key1") is None

    def test_remVal_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.remVal(sdb, b"nonexistent") is False

    def test_delVal_alias(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"key1", b"val1")
        assert dber.delVal(sdb, b"key1") is True

    def test_stores_are_isolated(self, dber):
        sdb1 = dber.env.open_db(b"evts.")
        sdb2 = dber.env.open_db(b"test.")
        dber.setVal(sdb1, b"key1", b"val_evts")
        dber.setVal(sdb2, b"key1", b"val_test")
        assert dber.getVal(sdb1, b"key1") == b"val_evts"
        assert dber.getVal(sdb2, b"key1") == b"val_test"


@needs_sqlitedbing
class TestOrdinalOps:
    """Ordinal (ON#) operations: putOnVal, pinOnVal, appendOnVal, etc."""

    def test_putOnVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putOnVal(sdb, b"pre", on=0, val=b"v0") is True
        assert dber.getOnVal(sdb, b"pre", on=0) == b"v0"

    def test_putOnVal_no_overwrite(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"v0")
        assert dber.putOnVal(sdb, b"pre", on=0, val=b"v1") is False
        assert dber.getOnVal(sdb, b"pre", on=0) == b"v0"

    def test_putOnVal_none_returns_false(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putOnVal(sdb, b"pre", on=0, val=None) is False

    def test_pinOnVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"v0")
        assert dber.pinOnVal(sdb, b"pre", on=0, val=b"v1") is True
        assert dber.getOnVal(sdb, b"pre", on=0) == b"v1"

    def test_pinOnVal_none_returns_false(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.pinOnVal(sdb, b"pre", on=0, val=None) is False

    def test_appendOnVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        on0 = dber.appendOnVal(sdb, b"pre", val=b"v0")
        on1 = dber.appendOnVal(sdb, b"pre", val=b"v1")
        assert on0 == 0
        assert on1 == 1
        assert dber.getOnVal(sdb, b"pre", on=0) == b"v0"
        assert dber.getOnVal(sdb, b"pre", on=1) == b"v1"

    def test_getOnItem(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=5, val=b"v5")
        result = dber.getOnItem(sdb, b"pre", on=5)
        assert result == (b"pre", 5, b"v5")

    def test_getOnItem_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getOnItem(sdb, b"pre", on=99) is None

    def test_getOnVal_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getOnVal(sdb, b"pre", on=99) is None

    def test_remOn(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"v0")
        assert dber.remOn(sdb, b"pre", on=0) is True
        assert dber.getOnVal(sdb, b"pre", on=0) is None

    def test_remOn_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.remOn(sdb, b"pre", on=0) is False

    def test_remOnAll(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"v0")
        dber.putOnVal(sdb, b"pre", on=1, val=b"v1")
        dber.putOnVal(sdb, b"pre", on=2, val=b"v2")
        assert dber.remOnAll(sdb, b"pre", on=1) is True
        assert dber.getOnVal(sdb, b"pre", on=0) == b"v0"
        assert dber.getOnVal(sdb, b"pre", on=1) is None
        assert dber.getOnVal(sdb, b"pre", on=2) is None

    def test_remOnAll_empty_key(self, dber):
        """remOnAll with empty key removes across all keys in subdb."""
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre1", on=0, val=b"v0")
        dber.putOnVal(sdb, b"pre2", on=0, val=b"v1")
        assert dber.remOnAll(sdb, key=b"", on=0) is True
        assert dber.getOnVal(sdb, b"pre1", on=0) is None
        assert dber.getOnVal(sdb, b"pre2", on=0) is None

    def test_cntOnAll(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"v0")
        dber.putOnVal(sdb, b"pre", on=1, val=b"v1")
        dber.putOnVal(sdb, b"pre", on=2, val=b"v2")
        assert dber.cntOnAll(sdb, b"pre") == 3
        assert dber.cntOnAll(sdb, b"pre", on=1) == 2

    def test_cntOnAll_empty_key(self, dber):
        """cntOnAll with empty key counts across all keys in subdb."""
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre1", on=0, val=b"v0")
        dber.putOnVal(sdb, b"pre2", on=0, val=b"v1")
        assert dber.cntOnAll(sdb, key=b"") == 2


@needs_sqlitedbing
class TestTopIteration:
    """Top-level iteration and management methods."""

    def test_getTopItemIter_all(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a.1", b"v1")
        dber.setVal(sdb, b"a.2", b"v2")
        dber.setVal(sdb, b"b.1", b"v3")
        items = list(dber.getTopItemIter(sdb, top=b""))
        assert len(items) == 3

    def test_getTopItemIter_prefix(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a.1", b"v1")
        dber.setVal(sdb, b"a.2", b"v2")
        dber.setVal(sdb, b"b.1", b"v3")
        items = list(dber.getTopItemIter(sdb, top=b"a."))
        assert len(items) == 2
        assert all(k.startswith(b"a.") for k, v in items)

    def test_getOnTopItemIter(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre.a", on=0, val=b"v0")
        dber.putOnVal(sdb, b"pre.a", on=1, val=b"v1")
        dber.putOnVal(sdb, b"pre.b", on=0, val=b"v2")
        items = list(dber.getOnTopItemIter(sdb, top=b"pre."))
        assert len(items) == 3
        assert items[0] == (b"pre.a", 0, b"v0")

    def test_getOnAllItemIter(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"v0")
        dber.putOnVal(sdb, b"pre", on=1, val=b"v1")
        dber.putOnVal(sdb, b"pre", on=2, val=b"v2")
        items = list(dber.getOnAllItemIter(sdb, b"pre", on=1))
        assert len(items) == 2
        assert items[0] == (b"pre", 1, b"v1")

    def test_remTop(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a.1", b"v1")
        dber.setVal(sdb, b"a.2", b"v2")
        dber.setVal(sdb, b"b.1", b"v3")
        assert dber.remTop(sdb, top=b"a.") is True
        assert dber.getVal(sdb, b"a.1") is None
        assert dber.getVal(sdb, b"b.1") == b"v3"

    def test_delTop_alias(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"x.1", b"v1")
        assert dber.delTop(sdb, top=b"x.") is True

    def test_cntTop(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a.1", b"v1")
        dber.setVal(sdb, b"a.2", b"v2")
        dber.setVal(sdb, b"b.1", b"v3")
        assert dber.cntTop(sdb, top=b"a.") == 2

    def test_cntAll(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"key1", b"v1")
        dber.setVal(sdb, b"key2", b"v2")
        assert dber.cntAll(sdb) == 2


@needs_sqlitedbing
class TestIoSetOps:
    """IoSet (Insertion-Ordered Set) operations."""

    def test_putIoSetVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putIoSetVals(sdb, b"key", [b"a", b"b", b"c"]) is True
        items = list(dber.getIoSetItemIter(sdb, b"key"))
        assert [v for k, v in items] == [b"a", b"b", b"c"]

    def test_putIoSetVals_no_duplicates(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key", [b"a", b"b"])
        dber.putIoSetVals(sdb, b"key", [b"b", b"c"])
        items = list(dber.getIoSetItemIter(sdb, b"key"))
        assert [v for k, v in items] == [b"a", b"b", b"c"]

    def test_pinIoSetVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key", [b"a", b"b"])
        dber.pinIoSetVals(sdb, b"key", [b"x", b"y"])
        items = list(dber.getIoSetItemIter(sdb, b"key"))
        assert [v for k, v in items] == [b"x", b"y"]

    def test_addIoSetVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.addIoSetVal(sdb, b"key", b"a") is True
        assert dber.addIoSetVal(sdb, b"key", b"a") is False
        assert dber.addIoSetVal(sdb, b"key", b"b") is True

    def test_getIoSetLastItem(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key", [b"a", b"b", b"c"])
        result = dber.getIoSetLastItem(sdb, b"key")
        assert result == (b"key", b"c")

    def test_getIoSetLastItem_empty(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getIoSetLastItem(sdb, b"missing") == ()

    def test_remIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key", [b"a", b"b"])
        assert dber.remIoSet(sdb, b"key") is True
        assert dber.cntIoSet(sdb, b"key") == 0

    def test_remIoSetVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key", [b"a", b"b", b"c"])
        assert dber.remIoSetVal(sdb, b"key", b"b") is True
        items = list(dber.getIoSetItemIter(sdb, b"key"))
        assert [v for k, v in items] == [b"a", b"c"]

    def test_remIoSetVal_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.remIoSetVal(sdb, b"key", b"x") is False

    def test_cntIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key", [b"a", b"b", b"c"])
        assert dber.cntIoSet(sdb, b"key") == 3

    def test_getTopIoSetItemIter(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"a.1", [b"v1", b"v2"])
        dber.putIoSetVals(sdb, b"a.2", [b"v3"])
        dber.putIoSetVals(sdb, b"b.1", [b"v4"])
        items = list(dber.getTopIoSetItemIter(sdb, top=b"a."))
        assert len(items) == 3


@needs_sqlitedbing
class TestOnIoSetOps:
    """OnIoSet (Ordinal + Insertion-Ordered Set) operations."""

    def test_putOnIoSetVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b"]) is True
        items = list(dber.getOnIoSetItemIter(sdb, b"key", on=0))
        assert [v for k, o, v in items] == [b"a", b"b"]

    def test_putOnIoSetVals_no_duplicates(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"b", b"c"])
        items = list(dber.getOnIoSetItemIter(sdb, b"key", on=0))
        assert [v for k, o, v in items] == [b"a", b"b", b"c"]

    def test_putOnIoSetVals_none_returns_false(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putOnIoSetVals(sdb, b"key", on=0, vals=None) is False

    def test_pinOnIoSetVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        assert dber.pinOnIoSetVals(sdb, b"key", on=0, vals=[b"x", b"y"]) is True
        items = list(dber.getOnIoSetItemIter(sdb, b"key", on=0))
        assert [v for k, o, v in items] == [b"x", b"y"]

    def test_pinOnIoSetVals_none_returns_false(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.pinOnIoSetVals(sdb, b"key", on=0, vals=None) is False

    def test_appendOnIoSetVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        on0 = dber.appendOnIoSetVals(sdb, b"key", [b"a", b"b"])
        on1 = dber.appendOnIoSetVals(sdb, b"key", [b"c"])
        assert on0 == 0
        assert on1 == 1

    def test_addOnIoSetVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.addOnIoSetVal(sdb, b"key", on=0, val=b"a") is True
        assert dber.addOnIoSetVal(sdb, b"key", on=0, val=b"a") is False
        assert dber.addOnIoSetVal(sdb, b"key", on=0, val=b"b") is True

    def test_addOnIoSetVal_none_returns_false(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.addOnIoSetVal(sdb, b"key", on=0, val=None) is False

    def test_getOnIoSetItemIter_with_ion(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b", b"c"])
        items = list(dber.getOnIoSetItemIter(sdb, b"key", on=0, ion=1))
        assert [v for k, o, v in items] == [b"b", b"c"]

    def test_getOnIoSetLastItem(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b", b"c"])
        result = dber.getOnIoSetLastItem(sdb, b"key", on=0)
        assert result == (b"key", 0, b"c")

    def test_getOnIoSetLastItem_empty(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getOnIoSetLastItem(sdb, b"missing", on=0) == ()

    def test_remOnIoSetVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        assert dber.remOnIoSetVal(sdb, b"key", on=0, val=b"a") is True
        items = list(dber.getOnIoSetItemIter(sdb, b"key", on=0))
        assert [v for k, o, v in items] == [b"b"]

    def test_remOnIoSetVal_all(self, dber):
        """remOnIoSetVal with val=None removes all entries at (key, on)."""
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        assert dber.remOnIoSetVal(sdb, b"key", on=0, val=None) is True
        items = list(dber.getOnIoSetItemIter(sdb, b"key", on=0))
        assert items == []

    def test_remOnIoSetVal_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.remOnIoSetVal(sdb, b"key", on=0, val=b"x") is False

    def test_remOnAllIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        dber.putOnIoSetVals(sdb, b"key", on=1, vals=[b"c"])
        dber.putOnIoSetVals(sdb, b"key", on=2, vals=[b"d"])
        assert dber.remOnAllIoSet(sdb, b"key", on=1) is True
        # on=0 should remain
        items0 = list(dber.getOnIoSetItemIter(sdb, b"key", on=0))
        assert [v for k, o, v in items0] == [b"a", b"b"]
        # on=1 and on=2 should be gone
        items1 = list(dber.getOnIoSetItemIter(sdb, b"key", on=1))
        assert items1 == []
        items2 = list(dber.getOnIoSetItemIter(sdb, b"key", on=2))
        assert items2 == []

    def test_remOnAllIoSet_empty_key(self, dber):
        """remOnAllIoSet with empty key removes across all keys."""
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key1", on=0, vals=[b"a"])
        dber.putOnIoSetVals(sdb, b"key2", on=0, vals=[b"b"])
        assert dber.remOnAllIoSet(sdb, key=b"", on=0) is True
        assert dber.cntOnIoSet(sdb, b"key1", on=0) == 0
        assert dber.cntOnIoSet(sdb, b"key2", on=0) == 0

    def test_cntOnIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b", b"c"])
        assert dber.cntOnIoSet(sdb, b"key", on=0) == 3

    def test_cntOnIoSet_with_ion(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b", b"c"])
        assert dber.cntOnIoSet(sdb, b"key", on=0, ion=1) == 2

    def test_cntOnAllIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        dber.putOnIoSetVals(sdb, b"key", on=1, vals=[b"c"])
        assert dber.cntOnAllIoSet(sdb, b"key") == 3

    def test_cntOnAllIoSet_from_on(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        dber.putOnIoSetVals(sdb, b"key", on=1, vals=[b"c"])
        assert dber.cntOnAllIoSet(sdb, b"key", on=1) == 1

    def test_cntOnAllIoSet_empty_key(self, dber):
        """cntOnAllIoSet with empty key counts across all keys."""
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key1", on=0, vals=[b"a"])
        dber.putOnIoSetVals(sdb, b"key2", on=0, vals=[b"b"])
        assert dber.cntOnAllIoSet(sdb, key=b"") == 2

    def test_different_on_values_are_isolated(self, dber):
        """Entries at different ordinal numbers are independent IoSets."""
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        dber.putOnIoSetVals(sdb, b"key", on=1, vals=[b"c", b"d", b"e"])
        items0 = list(dber.getOnIoSetItemIter(sdb, b"key", on=0))
        items1 = list(dber.getOnIoSetItemIter(sdb, b"key", on=1))
        assert [v for k, o, v in items0] == [b"a", b"b"]
        assert [v for k, o, v in items1] == [b"c", b"d", b"e"]


@needs_sqlitedbing
class TestDupOps:
    """Dup delegation methods (delegate to IoSet)."""

    def test_putVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putVals(sdb, b"key", [b"a", b"b"]) is True
        vals = dber.getVals(sdb, b"key")
        assert vals == [b"a", b"b"]

    def test_addVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.addVal(sdb, b"key", b"a") is True
        assert dber.addVal(sdb, b"key", b"a") is False

    def test_getValsIter(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putVals(sdb, b"key", [b"a", b"b", b"c"])
        vals = list(dber.getValsIter(sdb, b"key"))
        assert vals == [b"a", b"b", b"c"]

    def test_cntVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putVals(sdb, b"key", [b"a", b"b"])
        assert dber.cntVals(sdb, b"key") == 2

    def test_delVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putVals(sdb, b"key", [b"a", b"b"])
        assert dber.delVals(sdb, b"key") is True
        assert dber.cntVals(sdb, b"key") == 0


@needs_sqlitedbing
class TestIoDupOps:
    """IoDup delegation methods (delegate to IoSet)."""

    def test_putIoDupVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putIoDupVals(sdb, b"key", [b"a", b"b"]) is True

    def test_addIoDupVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.addIoDupVal(sdb, b"key", b"a") is True
        assert dber.addIoDupVal(sdb, b"key", b"a") is False

    def test_getIoDupVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoDupVals(sdb, b"key", [b"a", b"b"])
        assert dber.getIoDupVals(sdb, b"key") == [b"a", b"b"]

    def test_getIoDupValLast(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoDupVals(sdb, b"key", [b"a", b"b", b"c"])
        assert dber.getIoDupValLast(sdb, b"key") == b"c"

    def test_cntIoDups(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoDupVals(sdb, b"key", [b"a", b"b"])
        assert dber.cntIoDups(sdb, b"key") == 2

    def test_delIoDupVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoDupVals(sdb, b"key", [b"a", b"b"])
        assert dber.delIoDupVals(sdb, b"key") is True


@needs_sqlitedbing
class TestOnIoDupOps:
    """OnIoDup delegation methods (delegate to OnIoSet)."""

    def test_putOnIoDupVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putOnIoDupVals(sdb, b"key", on=0, vals=[b"a", b"b"]) is True
        vals = dber.getOnIoDupVals(sdb, b"key", on=0)
        assert vals == [b"a", b"b"]

    def test_addOnIoDupVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.addOnIoDupVal(sdb, b"key", on=0, val=b"a") is True
        assert dber.addOnIoDupVal(sdb, b"key", on=0, val=b"a") is False

    def test_appendOnIoDupVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        on0 = dber.appendOnIoDupVal(sdb, b"key", b"a")
        on1 = dber.appendOnIoDupVal(sdb, b"key", b"b")
        assert on0 == 0
        assert on1 == 1

    def test_getOnIoDupLast(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoDupVals(sdb, b"key", on=0, vals=[b"a", b"b", b"c"])
        assert dber.getOnIoDupLast(sdb, b"key", on=0) == b"c"

    def test_cntOnIoDups(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoDupVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        assert dber.cntOnIoDups(sdb, b"key", on=0) == 2

    def test_delOnIoDups(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoDupVals(sdb, b"key", on=0, vals=[b"a", b"b"])
        assert dber.delOnIoDups(sdb, b"key", on=0) is True
        assert dber.cntOnIoDups(sdb, b"key", on=0) == 0

    def test_delOnIoDupVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoDupVals(sdb, b"key", on=0, vals=[b"a", b"b", b"c"])
        assert dber.delOnIoDupVal(sdb, b"key", on=0, val=b"b") is True
        vals = dber.getOnIoDupVals(sdb, b"key", on=0)
        assert vals == [b"a", b"c"]
