"""Moto cloud tests for S3ArtifactStore (S3 CAS + DynamoDB first-seen)."""
import boto3
import pytest
from moto import mock_aws

from keri.db.dynamodbing import DynamoDBer
from keri_serviceaid.providers.artifact_store import S3ArtifactStore

PUB_STORE = "pub."


@pytest.fixture
def env():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="schema-cas")
        boto3.client("dynamodb", region_name="us-east-1")
        db = DynamoDBer.open(name="pub", stores=[PUB_STORE], table_name="keri-core",
                             namespace="schema-publisher:pub", region="us-east-1")
        yield s3, db
        db.close()


def test_store_writes_cas_object_and_claims_first_seen(env):
    s3, db = env
    store = S3ArtifactStore(bucket="schema-cas", db=db)
    r = store.store("ESaid", b'{"$id":"ESaid"}', by="EAlice")
    assert r.first_seen is True and r.first_publisher == "EAlice"
    obj = s3.get_object(Bucket="schema-cas", Key="oobi/ESaid")
    assert obj["Body"].read() == b'{"$id":"ESaid"}'
    assert obj["ContentType"] == "application/schema+json"


def test_second_publisher_not_first(env):
    s3, db = env
    store = S3ArtifactStore(bucket="schema-cas", db=db)
    store.store("ESaid", b'{"$id":"ESaid"}', by="EAlice")
    r = store.store("ESaid", b'{"$id":"ESaid"}', by="EBob")
    assert r.first_seen is False and r.first_publisher == "EAlice"
