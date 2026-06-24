"""DynamoLedger stores the prior grant bytes keyed by exn SAID (moto-backed)."""
import boto3
import pytest
from moto import mock_aws

from keri.db.dynamodbing import DynamoDBer
from keri_serviceaid import DynamoLedger
from keri_serviceaid.providers.idempotency import PROC_STORE


@pytest.fixture
def db():
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1")
        d = DynamoDBer.open(name="led", stores=[PROC_STORE],
                            table_name="keri-core", namespace="svc:kel",
                            region="us-east-1")
        yield d
        d.close()


def test_unseen_returns_none(db):
    assert DynamoLedger(db).seen("ENeverSeen") is None


def test_record_then_seen_roundtrips_grant_bytes(db):
    led = DynamoLedger(db)
    grant = b'{"v":"KERI10JSON","t":"exn"}-attachments'
    led.record("EReqSaid", grant)
    assert led.seen("EReqSaid") == grant


def test_record_overwrites(db):
    led = DynamoLedger(db)
    led.record("EReqSaid", b"first")
    led.record("EReqSaid", b"second")
    assert led.seen("EReqSaid") == b"second"


from keri_serviceaid import LMDBLedger


def test_lmdb_unseen_returns_none(issuer_hby):
    assert LMDBLedger(issuer_hby.db).seen("ENeverSeen") is None


def test_lmdb_record_then_seen_roundtrips_grant_bytes(issuer_hby):
    led = LMDBLedger(issuer_hby.db)
    grant = b'{"v":"KERI10JSON","t":"exn"}-attachments'
    led.record("EReqSaid", grant)
    assert led.seen("EReqSaid") == grant


def test_lmdb_record_overwrites(issuer_hby):
    led = LMDBLedger(issuer_hby.db)
    led.record("EReqSaid", b"first")
    led.record("EReqSaid", b"second")
    assert led.seen("EReqSaid") == b"second"
