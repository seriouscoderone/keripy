# -*- encoding: utf-8 -*-
"""
tests.db.test_dynamodbing module

Tests for DynamoDB-backed DynamoDBer using moto mock.
"""

import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

try:
    from keri.db.dynamodbing import (
        DynamoDBer,
        DynamoSubDb,
        DynamoEnv,
        openDynamoDB,
        onKey,
        splitOnKey,
        suffix,
        unsuffix,
        MaxON,
    )
    HAS_DYNAMODBING = True
except ImportError:
    HAS_DYNAMODBING = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")
needs_dynamodbing = pytest.mark.skipif(not HAS_DYNAMODBING, reason="requires dynamodbing + boto3")

STORES = ["evts.", "fels.", "kels.", "sigs.", "test."]


@pytest.fixture
def dber():
    """Provides a DynamoDBer instance backed by moto."""
    if not HAS_MOTO or not HAS_DYNAMODBING:
        pytest.skip("requires moto and dynamodbing")
    with mock_aws():
        db = DynamoDBer.open(
            name="test",
            stores=STORES,
            region="us-east-1",
        )
        yield db
        db.close(clear=True)


@needs_moto
@needs_dynamodbing
class TestDynamoDBerLifecycle:
    """Test open/close/version lifecycle."""

    def test_open_creates_table(self):
        with mock_aws():
            db = DynamoDBer.open(name="lifecycle", stores=["core."],
                                 region="us-east-1")
            assert db is not None
            assert db.name == "lifecycle"
            assert "core." in db.stores
            db.close()

    def test_open_with_clear(self):
        with mock_aws():
            db = DynamoDBer.open(name="cleartest", stores=["core."],
                                 region="us-east-1")
            sdb = db.env.open_db(b"core.")
            db.setVal(sdb, b"key1", b"val1")
            db.close()

            db2 = DynamoDBer.open(name="cleartest", stores=["core."],
                                  region="us-east-1", clear=True)
            sdb2 = db2.env.open_db(b"core.")
            assert db2.getVal(sdb2, b"key1") is None
            db2.close()

    def test_version(self, dber):
        assert dber.version is None
        dber.version = "1.2.3"
        assert dber.version == "1.2.3"

    def test_close_clears(self):
        with mock_aws():
            db = DynamoDBer.open(name="closetest", stores=["core."],
                                 region="us-east-1")
            sdb = db.env.open_db(b"core.")
            db.setVal(sdb, b"key1", b"val1")
            db.close(clear=True)
            assert db.stores == []

    def test_env_open_db(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert isinstance(sdb, DynamoSubDb)
        assert sdb.opened is True

    def test_env_open_db_unknown(self, dber):
        with pytest.raises(KeyError):
            dber.env.open_db(b"nonexistent.")

    def test_env_open_db_dupsort(self, dber):
        sdb = dber.env.open_db(b"test.", dupsort=True)
        assert sdb.flags() == {"dupsort": True}

    def test_flush_noop(self, dber):
        assert dber.flush() == 0


@needs_moto
@needs_dynamodbing
class TestSingleValueCRUD:
    """Test putVal, setVal, getVal, remVal."""

    def test_putVal_insert(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putVal(sdb, b"key1", b"val1") is True
        assert dber.getVal(sdb, b"key1") == b"val1"

    def test_putVal_no_overwrite(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putVal(sdb, b"key1", b"val1")
        assert dber.putVal(sdb, b"key1", b"val2") is False
        assert dber.getVal(sdb, b"key1") == b"val1"

    def test_putVal_empty_key_raises(self, dber):
        sdb = dber.env.open_db(b"test.")
        with pytest.raises(KeyError):
            dber.putVal(sdb, b"", b"val1")

    def test_setVal_insert(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.setVal(sdb, b"key1", b"val1") is True
        assert dber.getVal(sdb, b"key1") == b"val1"

    def test_setVal_overwrite(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"key1", b"val1")
        dber.setVal(sdb, b"key1", b"val2")
        assert dber.getVal(sdb, b"key1") == b"val2"

    def test_setVal_empty_key_raises(self, dber):
        sdb = dber.env.open_db(b"test.")
        with pytest.raises(KeyError):
            dber.setVal(sdb, b"", b"val1")

    def test_getVal_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getVal(sdb, b"missing") is None

    def test_getVal_empty_key_raises(self, dber):
        sdb = dber.env.open_db(b"test.")
        with pytest.raises(KeyError):
            dber.getVal(sdb, b"")

    def test_remVal_exists(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"key1", b"val1")
        assert dber.remVal(sdb, b"key1") is True
        assert dber.getVal(sdb, b"key1") is None

    def test_remVal_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.remVal(sdb, b"missing") is False

    def test_remVal_empty_key(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.remVal(sdb, b"") is False

    def test_delVal_alias(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"key1", b"val1")
        assert dber.delVal(sdb, b"key1") is True


@needs_moto
@needs_dynamodbing
class TestOrdinalOps:
    """Test putOnVal, pinOnVal, appendOnVal, getOnItem, getOnVal, remOn, remOnAll, cntOnAll."""

    def test_putOnVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putOnVal(sdb, b"pre", on=0, val=b"evt0") is True
        assert dber.getOnVal(sdb, b"pre", on=0) == b"evt0"

    def test_putOnVal_none(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putOnVal(sdb, b"pre", on=0, val=None) is False

    def test_putOnVal_no_overwrite(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"evt0")
        assert dber.putOnVal(sdb, b"pre", on=0, val=b"evt1") is False

    def test_pinOnVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"evt0")
        assert dber.pinOnVal(sdb, b"pre", on=0, val=b"evt1") is True
        assert dber.getOnVal(sdb, b"pre", on=0) == b"evt1"

    def test_pinOnVal_none(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.pinOnVal(sdb, b"pre", on=0, val=None) is False

    def test_pinOnVal_empty_key(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.pinOnVal(sdb, b"", on=0, val=b"v") is False

    def test_appendOnVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        on0 = dber.appendOnVal(sdb, b"pre", val=b"evt0")
        on1 = dber.appendOnVal(sdb, b"pre", val=b"evt1")
        on2 = dber.appendOnVal(sdb, b"pre", val=b"evt2")
        assert on0 == 0
        assert on1 == 1
        assert on2 == 2
        assert dber.getOnVal(sdb, b"pre", on=0) == b"evt0"
        assert dber.getOnVal(sdb, b"pre", on=1) == b"evt1"
        assert dber.getOnVal(sdb, b"pre", on=2) == b"evt2"

    def test_appendOnVal_empty_key_raises(self, dber):
        sdb = dber.env.open_db(b"test.")
        with pytest.raises(ValueError):
            dber.appendOnVal(sdb, b"", val=b"v")

    def test_appendOnVal_none_val_raises(self, dber):
        sdb = dber.env.open_db(b"test.")
        with pytest.raises(ValueError):
            dber.appendOnVal(sdb, b"pre", val=None)

    def test_appendOnVal_retries_past_taken_ordinals(self, dber):
        """Under a stale GSI (concurrent-writer race), appendOnVal lands at the first
        genuinely-free ordinal via conditional-put retry, not raise/overwrite."""
        sdb = dber.env.open_db(b"test.")
        assert dber.appendOnVal(sdb, b"pre", val=b"evt0") == 0
        assert dber.appendOnVal(sdb, b"pre", val=b"evt1") == 1
        dber._query_gsi = lambda *a, **k: []   # simulate GSI lag: max-query reports empty
        on = dber.appendOnVal(sdb, b"pre", val=b"evt2")
        assert on == 2
        assert dber.getOnVal(sdb, b"pre", on=0) == b"evt0"
        assert dber.getOnVal(sdb, b"pre", on=1) == b"evt1"
        assert dber.getOnVal(sdb, b"pre", on=2) == b"evt2"

    def test_getOnItem(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=5, val=b"evt5")
        result = dber.getOnItem(sdb, b"pre", on=5)
        assert result == (b"pre", 5, b"evt5")

    def test_getOnItem_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getOnItem(sdb, b"pre", on=99) is None

    def test_getOnItem_empty_key(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getOnItem(sdb, b"", on=0) is None

    def test_getOnVal_empty_key(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getOnVal(sdb, b"", on=0) is None

    def test_remOn(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"evt0")
        assert dber.remOn(sdb, b"pre", on=0) is True
        assert dber.getOnVal(sdb, b"pre", on=0) is None

    def test_remOn_empty_key(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.remOn(sdb, b"", on=0) is False

    def test_remOnAll(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.appendOnVal(sdb, b"pre", val=b"evt0")
        dber.appendOnVal(sdb, b"pre", val=b"evt1")
        dber.appendOnVal(sdb, b"pre", val=b"evt2")
        # Remove from on=1 onwards
        assert dber.remOnAll(sdb, b"pre", on=1) is True
        assert dber.getOnVal(sdb, b"pre", on=0) == b"evt0"
        assert dber.getOnVal(sdb, b"pre", on=1) is None
        assert dber.getOnVal(sdb, b"pre", on=2) is None

    def test_remOnAll_empty_key(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.appendOnVal(sdb, b"pre", val=b"evt0")
        assert dber.remOnAll(sdb, b"") is True

    def test_cntOnAll(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.appendOnVal(sdb, b"pre", val=b"evt0")
        dber.appendOnVal(sdb, b"pre", val=b"evt1")
        dber.appendOnVal(sdb, b"pre", val=b"evt2")
        assert dber.cntOnAll(sdb, b"pre") == 3
        assert dber.cntOnAll(sdb, b"pre", on=1) == 2

    def test_cntOnAll_empty_key(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a", b"v1")
        dber.setVal(sdb, b"b", b"v2")
        assert dber.cntOnAll(sdb, b"") == 2


@needs_moto
@needs_dynamodbing
class TestTopIteration:
    """Test getTopItemIter, getOnTopItemIter, getOnAllItemIter, remTop, cntTop, cntAll."""

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
        keys = [k for k, _ in items]
        assert b"a.1" in keys
        assert b"a.2" in keys

    def test_getOnTopItemIter(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"e0")
        dber.putOnVal(sdb, b"pre", on=1, val=b"e1")
        items = list(dber.getOnTopItemIter(sdb, top=b"pre"))
        assert len(items) == 2
        assert items[0] == (b"pre", 0, b"e0")
        assert items[1] == (b"pre", 1, b"e1")

    def test_getOnAllItemIter(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"pre", on=0, val=b"e0")
        dber.putOnVal(sdb, b"pre", on=1, val=b"e1")
        dber.putOnVal(sdb, b"pre", on=2, val=b"e2")
        # From on=1
        items = list(dber.getOnAllItemIter(sdb, key=b"pre", on=1))
        assert len(items) == 2
        assert items[0] == (b"pre", 1, b"e1")
        assert items[1] == (b"pre", 2, b"e2")

    def test_getOnAllItemIter_whole_db(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnVal(sdb, b"a", on=0, val=b"e0")
        dber.putOnVal(sdb, b"b", on=0, val=b"e1")
        items = list(dber.getOnAllItemIter(sdb, key=b""))
        assert len(items) == 2

    def test_remTop(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a.1", b"v1")
        dber.setVal(sdb, b"a.2", b"v2")
        dber.setVal(sdb, b"b.1", b"v3")
        assert dber.remTop(sdb, top=b"a.") is True
        items = list(dber.getTopItemIter(sdb, top=b""))
        assert len(items) == 1
        assert items[0][0] == b"b.1"

    def test_remTop_all(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a", b"v1")
        assert dber.remTop(sdb, top=b"") is True
        assert dber.cntAll(sdb) == 0

    def test_remTop_empty(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.remTop(sdb, top=b"x") is False

    def test_delTop_alias(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a", b"v1")
        assert dber.delTop(sdb, top=b"a") is True

    def test_cntTop(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a.1", b"v1")
        dber.setVal(sdb, b"a.2", b"v2")
        dber.setVal(sdb, b"b.1", b"v3")
        assert dber.cntTop(sdb, top=b"a.") == 2
        assert dber.cntTop(sdb, top=b"") == 3

    def test_cntAll(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.setVal(sdb, b"a", b"v1")
        dber.setVal(sdb, b"b", b"v2")
        assert dber.cntAll(sdb) == 2


@needs_moto
@needs_dynamodbing
class TestIoSetOps:
    """Test insertion-ordered set operations."""

    def test_putIoSetVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putIoSetVals(sdb, b"key1", [b"a", b"b", b"c"]) is True
        items = list(dber.getIoSetItemIter(sdb, b"key1"))
        vals = [v for _, v in items]
        assert vals == [b"a", b"b", b"c"]

    def test_putIoSetVals_no_duplicates(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key1", [b"a", b"b"])
        dber.putIoSetVals(sdb, b"key1", [b"b", b"c"])  # b already exists
        items = list(dber.getIoSetItemIter(sdb, b"key1"))
        vals = [v for _, v in items]
        assert vals == [b"a", b"b", b"c"]

    def test_pinIoSetVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key1", [b"a", b"b"])
        dber.pinIoSetVals(sdb, b"key1", [b"x", b"y"])
        items = list(dber.getIoSetItemIter(sdb, b"key1"))
        vals = [v for _, v in items]
        assert vals == [b"x", b"y"]

    def test_addIoSetVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.addIoSetVal(sdb, b"key1", b"a") is True
        assert dber.addIoSetVal(sdb, b"key1", b"b") is True
        assert dber.addIoSetVal(sdb, b"key1", b"a") is False  # duplicate
        items = list(dber.getIoSetItemIter(sdb, b"key1"))
        vals = [v for _, v in items]
        assert vals == [b"a", b"b"]

    def test_getIoSetLastItem(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key1", [b"a", b"b", b"c"])
        result = dber.getIoSetLastItem(sdb, b"key1")
        assert result == (b"key1", b"c")

    def test_getIoSetLastItem_empty(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.getIoSetLastItem(sdb, b"key1") == ()
        assert dber.getIoSetLastItem(sdb, b"") == ()

    def test_remIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key1", [b"a", b"b"])
        assert dber.remIoSet(sdb, b"key1") is True
        items = list(dber.getIoSetItemIter(sdb, b"key1"))
        assert items == []

    def test_remIoSetVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key1", [b"a", b"b", b"c"])
        assert dber.remIoSetVal(sdb, b"key1", b"b") is True
        items = list(dber.getIoSetItemIter(sdb, b"key1"))
        vals = [v for _, v in items]
        assert b"b" not in vals
        assert b"a" in vals
        assert b"c" in vals

    def test_remIoSetVal_missing(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.remIoSetVal(sdb, b"key1", b"x") is False

    def test_cntIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"key1", [b"a", b"b", b"c"])
        assert dber.cntIoSet(sdb, b"key1") == 3

    def test_getTopIoSetItemIter(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"k1", [b"a", b"b"])
        dber.putIoSetVals(sdb, b"k2", [b"c"])
        items = list(dber.getTopIoSetItemIter(sdb, top=b"k"))
        assert len(items) == 3

    def test_getIoSetLastItemIterAll(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putIoSetVals(sdb, b"k1", [b"a", b"b"])
        dber.putIoSetVals(sdb, b"k2", [b"c", b"d"])
        items = list(dber.getIoSetLastItemIterAll(sdb))
        # Should yield last of each group: (k1, b), (k2, d)
        assert len(items) == 2
        vals = [v for _, v in items]
        assert b"b" in vals
        assert b"d" in vals


@needs_moto
@needs_dynamodbing
class TestOnIoSetOps:
    """Test On + IoSet combined operations."""

    def test_putOnIoSetVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.putOnIoSetVals(sdb, b"pre", on=0, vals=[b"a", b"b"]) is True
        items = list(dber.getOnIoSetItemIter(sdb, b"pre", on=0))
        vals = [v for _, _, v in items]
        assert vals == [b"a", b"b"]

    def test_appendOnIoSetVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        on0 = dber.appendOnIoSetVals(sdb, b"pre", [b"a", b"b"])
        on1 = dber.appendOnIoSetVals(sdb, b"pre", [b"c"])
        assert on0 == 0
        assert on1 == 1

    def test_addOnIoSetVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.addOnIoSetVal(sdb, b"pre", on=0, val=b"a") is True
        assert dber.addOnIoSetVal(sdb, b"pre", on=0, val=b"a") is False  # dup

    def test_getOnIoSetLastItem(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"pre", on=0, vals=[b"a", b"b", b"c"])
        result = dber.getOnIoSetLastItem(sdb, b"pre", on=0)
        assert result == (b"pre", 0, b"c")

    def test_remOnIoSetVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"pre", on=0, vals=[b"a", b"b"])
        assert dber.remOnIoSetVal(sdb, b"pre", on=0, val=b"a") is True
        items = list(dber.getOnIoSetItemIter(sdb, b"pre", on=0))
        vals = [v for _, _, v in items]
        assert vals == [b"b"]

    def test_remOnAllIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"pre", on=0, vals=[b"a"])
        dber.putOnIoSetVals(sdb, b"pre", on=1, vals=[b"b"])
        assert dber.remOnAllIoSet(sdb, b"pre", on=1) is True
        # on=0 should still exist
        items0 = list(dber.getOnIoSetItemIter(sdb, b"pre", on=0))
        assert len(items0) == 1

    def test_cntOnIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"pre", on=0, vals=[b"a", b"b"])
        assert dber.cntOnIoSet(sdb, b"pre", on=0) == 2

    def test_cntOnAllIoSet(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putOnIoSetVals(sdb, b"pre", on=0, vals=[b"a"])
        dber.putOnIoSetVals(sdb, b"pre", on=1, vals=[b"b", b"c"])
        assert dber.cntOnAllIoSet(sdb, b"pre") == 3


@needs_moto
@needs_dynamodbing
class TestDupOps:
    """Test Dup methods (mapped to IoSet internally)."""

    def test_putVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putVals(sdb, b"key1", [b"a", b"b"])
        vals = dber.getVals(sdb, b"key1")
        assert vals == [b"a", b"b"]

    def test_addVal(self, dber):
        sdb = dber.env.open_db(b"test.")
        assert dber.addVal(sdb, b"key1", b"a") is True
        assert dber.addVal(sdb, b"key1", b"a") is False

    def test_getValsIter(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putVals(sdb, b"key1", [b"a", b"b"])
        vals = list(dber.getValsIter(sdb, b"key1"))
        assert vals == [b"a", b"b"]

    def test_cntVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putVals(sdb, b"key1", [b"a", b"b", b"c"])
        assert dber.cntVals(sdb, b"key1") == 3

    def test_delVals(self, dber):
        sdb = dber.env.open_db(b"test.")
        dber.putVals(sdb, b"key1", [b"a", b"b"])
        assert dber.delVals(sdb, b"key1") is True
        assert dber.getVals(sdb, b"key1") == []


@needs_moto
@needs_dynamodbing
class TestContextManager:
    """Test openDynamoDB context manager."""

    def test_open_and_use(self):
        with mock_aws():
            with openDynamoDB(name="ctx", stores=["test."],
                              region="us-east-1") as db:
                sdb = db.env.open_db(b"test.")
                db.setVal(sdb, b"key1", b"val1")
                assert db.getVal(sdb, b"key1") == b"val1"

    def test_temp_clears_on_exit(self):
        with mock_aws():
            with openDynamoDB(name="ctx", stores=["test."],
                              region="us-east-1", temp=True) as db:
                sdb = db.env.open_db(b"test.")
                db.setVal(sdb, b"key1", b"val1")
            # After exit, stores should be cleared
            assert db.stores == []


@needs_moto
@needs_dynamodbing
class TestKeyUtilities:
    """Test key composition utility functions."""

    def test_onKey(self):
        result = onKey(b"pre", 42)
        assert result == b"pre.0000000000000000000000000000002a"

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
