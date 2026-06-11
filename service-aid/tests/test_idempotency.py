import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri.db.dynamodbing import DynamoDBer
from serviceaid.idempotency import Ledger, PROC_STORE

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


@needs_moto
def test_record_then_seen():
    with mock_aws():
        db = DynamoDBer.open(name="svc", stores=[PROC_STORE], region="us-east-1",
                             table_name="core", namespace="rating:proc")
        ledger = Ledger(db)
        assert ledger.seen("Emsg1") is None
        ledger.record("Emsg1", {"status": "ok", "credential": "Ecred1"})
        assert ledger.seen("Emsg1") == {"status": "ok", "credential": "Ecred1"}
        db.close()


@needs_moto
def test_unseen_message_returns_none():
    with mock_aws():
        db = DynamoDBer.open(name="svc", stores=[PROC_STORE], region="us-east-1",
                             table_name="core", namespace="rating:proc")
        assert Ledger(db).seen("nope") is None
        db.close()
