"""Tests for DynamoDBer.claimFirstSeen — conditional first-seen primitive.

Uses moto mock_aws (mirrors tests/serviceaid/test_providers_idempotency.py).
"""
import boto3
import pytest
from moto import mock_aws

from keri.db.dynamodbing import DynamoDBer

PUB_STORE = "pub."


@pytest.fixture
def db():
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1")
        d = DynamoDBer.open(name="pub", stores=[PUB_STORE],
                            table_name="keri-core", namespace="schema-publisher:pub",
                            region="us-east-1")
        yield d
        d.close()


def test_first_claim_wins_second_reads_existing(db):
    ok, existing = db.claimFirstSeen(PUB_STORE, b"ESaid", b"EAlice")
    assert ok is True and existing is None
    ok2, existing2 = db.claimFirstSeen(PUB_STORE, b"ESaid", b"EBob")
    assert ok2 is False
    assert existing2 == b"EAlice"
