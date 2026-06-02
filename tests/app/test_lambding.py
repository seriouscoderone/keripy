# -*- encoding: utf-8 -*-
"""
tests.app.test_lambding module

Integration tests for Lambda-compatible keripy using DynamoDB backend.
"""

import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

try:
    from keri.db.dynamodbing import DynamoDBer
    from keri.app.lambding import (
        BASER_STORES, KEEPER_STORES, REGER_STORES, NOTER_STORES, MAILBOXER_STORES,
        setup_baser, setup_keeper, setup_reger, setup_noter, setup_mailboxer,
    )
    HAS_LAMBDING = True
except ImportError:
    HAS_LAMBDING = False

needs = pytest.mark.skipif(
    not (HAS_MOTO and HAS_LAMBDING),
    reason="requires moto and keri.app.lambding",
)


@pytest.fixture
def dynamo_baser():
    if not HAS_MOTO or not HAS_LAMBDING:
        pytest.skip("requires moto and lambding")
    with mock_aws():
        dber = DynamoDBer.open(name="test-db", stores=BASER_STORES, region="us-east-1")
        setup_baser(dber)
        yield dber
        dber.close(clear=True)


@pytest.fixture
def dynamo_keeper():
    if not HAS_MOTO or not HAS_LAMBDING:
        pytest.skip("requires moto and lambding")
    with mock_aws():
        dber = DynamoDBer.open(name="test-ks", stores=KEEPER_STORES, region="us-east-1")
        setup_keeper(dber)
        yield dber
        dber.close(clear=True)


@needs
class TestSetupBaser:
    """Test that setup_baser attaches all expected sub-databases."""

    def test_event_subdbs(self, dynamo_baser):
        db = dynamo_baser
        assert hasattr(db, "evts")
        assert hasattr(db, "fels")
        assert hasattr(db, "kels")
        assert hasattr(db, "dtss")
        assert hasattr(db, "aess")

    def test_signature_subdbs(self, dynamo_baser):
        db = dynamo_baser
        assert hasattr(db, "sigs")
        assert hasattr(db, "wigs")
        assert hasattr(db, "rcts")

    def test_escrow_subdbs(self, dynamo_baser):
        db = dynamo_baser
        assert hasattr(db, "pses")
        assert hasattr(db, "pwes")
        assert hasattr(db, "pdes")
        assert hasattr(db, "ooes")
        assert hasattr(db, "dels")

    def test_state_subdbs(self, dynamo_baser):
        db = dynamo_baser
        assert hasattr(db, "states")
        assert hasattr(db, "habs")
        assert hasattr(db, "names")
        assert hasattr(db, "wits")

    def test_baser_state(self, dynamo_baser):
        db = dynamo_baser
        assert hasattr(db, "prefixes")
        assert hasattr(db, "groups")
        assert hasattr(db, "kevers")
        assert hasattr(db, "_kevers")

    def test_kram_subdbs(self, dynamo_baser):
        db = dynamo_baser
        assert hasattr(db, "kramCTYP")
        assert hasattr(db, "kramMSGC")
        assert hasattr(db, "kramPMKS")

    def test_oobi_subdbs(self, dynamo_baser):
        db = dynamo_baser
        assert hasattr(db, "oobis")
        assert hasattr(db, "eoobi")
        assert hasattr(db, "roobi")

    def test_opened_temp(self, dynamo_baser):
        assert dynamo_baser.opened is True
        assert dynamo_baser.temp is False


@needs
class TestSetupKeeper:
    """Test that setup_keeper attaches all expected sub-databases."""

    def test_all_subdbs(self, dynamo_keeper):
        ks = dynamo_keeper
        assert hasattr(ks, "gbls")
        assert hasattr(ks, "pris")
        assert hasattr(ks, "prxs")
        assert hasattr(ks, "nxts")
        assert hasattr(ks, "smids")
        assert hasattr(ks, "rmids")
        assert hasattr(ks, "pres")
        assert hasattr(ks, "prms")
        assert hasattr(ks, "sits")
        assert hasattr(ks, "pubs")


@needs
class TestSetupReger:
    """Test that setup_reger attaches all expected sub-databases."""

    def test_reger_subdbs(self):
        with mock_aws():
            dber = DynamoDBer.open(name="test-rg", stores=REGER_STORES, region="us-east-1")
            setup_reger(dber)
            assert hasattr(dber, "tvts")
            assert hasattr(dber, "tels")
            assert hasattr(dber, "creds")
            assert hasattr(dber, "txnsb")
            assert hasattr(dber, "regs")
            assert hasattr(dber, "saved")
            dber.close(clear=True)


@needs
class TestSetupNoter:
    """Test that setup_noter attaches sub-databases and business methods."""

    def test_noter_subdbs_and_methods(self):
        with mock_aws():
            dber = DynamoDBer.open(name="test-nt", stores=NOTER_STORES, region="us-east-1")
            setup_noter(dber)
            assert hasattr(dber, "notes")
            assert hasattr(dber, "nidx")
            assert hasattr(dber, "ncigs")
            assert callable(getattr(dber, "add", None))
            assert callable(getattr(dber, "update", None))
            assert callable(getattr(dber, "rem", None))
            assert callable(getattr(dber, "get", None))
            assert callable(getattr(dber, "getNotes", None))
            assert callable(getattr(dber, "getNoteCnt", None))
            dber.close(clear=True)


@needs
class TestSetupMailboxer:
    """Test that setup_mailboxer attaches sub-databases and business methods."""

    def test_mailboxer_subdbs_and_methods(self):
        with mock_aws():
            dber = DynamoDBer.open(name="test-mx", stores=MAILBOXER_STORES, region="us-east-1")
            setup_mailboxer(dber)
            assert hasattr(dber, "tpcs")
            assert hasattr(dber, "msgs")
            assert callable(getattr(dber, "appendToTopic", None))
            assert callable(getattr(dber, "getTopicMsgs", None))
            assert callable(getattr(dber, "storeMsg", None))
            assert callable(getattr(dber, "cloneTopicIter", None))
            dber.close(clear=True)


@needs
class TestHaberyIntegration:
    """Test full Habery lifecycle with DynamoDB backends."""

    def test_create_habery(self):
        """Habery initializes with DynamoDB-backed db and ks."""
        from keri.app.habbing import Habery
        from keri.core.signing import Salter

        with mock_aws():
            db = setup_baser(DynamoDBer.open(name="hab-db", stores=BASER_STORES, region="us-east-1"))
            ks = setup_keeper(DynamoDBer.open(name="hab-ks", stores=KEEPER_STORES, region="us-east-1"))

            salt = Salter().qb64
            hby = Habery(name="test", temp=False, free=True, db=db, ks=ks, salt=salt)
            assert hby.inited is True
            assert hby.db.opened is True
            hby.close()

    def test_make_hab_inception(self):
        """Create a Hab and verify inception event is stored in DynamoDB."""
        from keri.app.habbing import Habery
        from keri.core.signing import Salter

        with mock_aws():
            db = setup_baser(DynamoDBer.open(name="hab-db", stores=BASER_STORES, region="us-east-1"))
            ks = setup_keeper(DynamoDBer.open(name="hab-ks", stores=KEEPER_STORES, region="us-east-1"))

            salt = Salter().qb64
            hby = Habery(name="test", temp=False, free=True, db=db, ks=ks, salt=salt)

            hab = hby.makeHab(name="alice", icount=1, isith="1",
                              ncount=1, nsith="1", transferable=True)

            assert hab.pre is not None
            assert hab.name == "alice"

            # Verify kever exists
            kever = hab.kever
            assert kever.sner.num == 0
            assert len(kever.verfers) == 1

            # Verify state stored in DynamoDB
            state = db.states.get(keys=(hab.pre,))
            assert state is not None
            assert state.i == hab.pre

            # Verify event stored
            evt = db.evts.get(keys=(hab.pre, hab.kever.serder.said))
            assert evt is not None

            hby.close()

    def test_habery_reload(self):
        """Create a Hab, close, reopen, and verify it's still there."""
        from keri.app.habbing import Habery
        from keri.core.signing import Salter

        with mock_aws():
            # Create
            db = setup_baser(DynamoDBer.open(name="reload-db", stores=BASER_STORES, region="us-east-1"))
            ks = setup_keeper(DynamoDBer.open(name="reload-ks", stores=KEEPER_STORES, region="us-east-1"))

            salt = Salter().qb64
            hby = Habery(name="test", temp=False, free=True, db=db, ks=ks, salt=salt)
            hab = hby.makeHab(name="bob", icount=1, isith="1",
                              ncount=1, nsith="1", transferable=True)
            pre = hab.pre
            hby.close()

            # Reopen with same DynamoDB tables (data persists in moto within same mock_aws)
            db2 = setup_baser(DynamoDBer.open(name="reload-db", stores=BASER_STORES, region="us-east-1"))
            ks2 = setup_keeper(DynamoDBer.open(name="reload-ks", stores=KEEPER_STORES, region="us-east-1"))

            hby2 = Habery(name="test", temp=False, free=True, db=db2, ks=ks2, salt=salt)

            # Verify Hab was reloaded
            hab2 = hby2.habByName("bob")
            assert hab2 is not None
            assert hab2.pre == pre

            hby2.close()


@needs
class TestIoDupMethods:
    """Test that IoDup method aliases work correctly."""

    def test_addOnIoDupVal(self):
        with mock_aws():
            dber = DynamoDBer.open(name="iodup-test", stores=["test."], region="us-east-1")
            sdb = dber.env.open_db(b"test.")

            assert dber.addOnIoDupVal(sdb, b"pre", on=0, val=b"val1") is True
            assert dber.addOnIoDupVal(sdb, b"pre", on=0, val=b"val2") is True
            assert dber.addOnIoDupVal(sdb, b"pre", on=0, val=b"val1") is False  # dup

            vals = dber.getOnIoDupVals(sdb, b"pre", on=0)
            assert b"val1" in vals
            assert b"val2" in vals

            dber.close(clear=True)

    def test_putIoDupVals(self):
        with mock_aws():
            dber = DynamoDBer.open(name="iodup-test", stores=["test."], region="us-east-1")
            sdb = dber.env.open_db(b"test.")

            dber.putIoDupVals(sdb, b"key", [b"a", b"b", b"c"])
            vals = dber.getIoDupVals(sdb, b"key")
            assert vals == [b"a", b"b", b"c"]

            dber.close(clear=True)
