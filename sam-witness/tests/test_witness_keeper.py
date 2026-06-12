"""Witness keeper-on-SecretKeeper tests (Task 7).

Mocks BOTH DynamoDB and Secrets Manager in-process with moto's `mock_aws`,
then drives `witness_handler.init()` through a cold start and a simulated
destroy-replace redeploy.

The load-bearing assertion is `pre2 == pre1`: on redeploy CloudFormation wipes
the Baser `-db` table (recreated EMPTY) but the keeper SECRET survives with the
ORIGINAL salt. The witness is non-transferable and salty-derived, so
re-incepting from the preserved salt MUST reproduce the identical AID — proving
destroy-replace safety.
"""
import json
import os
import sys

import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")

# Make sam-witness/ importable so `import witness_handler` resolves.
_SAM_WITNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SAM_WITNESS not in sys.path:
    sys.path.insert(0, _SAM_WITNESS)

REGION = "us-east-1"
BASER_TABLE = "witness-test-db"
KEEPER_SECRET = "keri/witness-test/keeper"


def _create_baser_table():
    """Create the witness Baser DynamoDB table mirroring template.yaml's
    PK/SK + subdb-index GSI schema. (No keeper table — that's gone.)"""
    import boto3
    client = boto3.client("dynamodb", region_name=REGION)
    client.create_table(
        TableName=BASER_TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "gsi_pk", "AttributeType": "S"},
            {"AttributeName": "gsi_sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "subdb-index",
            "KeySchema": [
                {"AttributeName": "gsi_pk", "KeyType": "HASH"},
                {"AttributeName": "gsi_sk", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )
    client.get_waiter("table_exists").wait(TableName=BASER_TABLE)


def _drop_baser_table():
    import boto3
    client = boto3.client("dynamodb", region_name=REGION)
    client.delete_table(TableName=BASER_TABLE)
    client.get_waiter("table_not_exists").wait(TableName=BASER_TABLE)


def _read_keeper_secret():
    import boto3
    resp = boto3.client("secretsmanager", region_name=REGION
                        ).get_secret_value(SecretId=KEEPER_SECRET)
    return json.loads(resp["SecretString"])


def _set_env(monkeypatch):
    monkeypatch.setenv("WITNESS_NAME", "witness-test")
    monkeypatch.setenv("WITNESS_ALIAS", "witness")
    monkeypatch.setenv("WITNESS_REGION", REGION)
    monkeypatch.setenv("WITNESS_URL", "")          # skip URL-registration block
    monkeypatch.setenv("WITNESS_KEEPER_SECRET", KEEPER_SECRET)
    monkeypatch.setenv("WITNESS_BASER_TABLE", BASER_TABLE)
    # Leave WITNESS_ENDPOINT_URL / WITNESS_SECRET_ENDPOINT_URL UNSET so moto
    # intercepts the default AWS endpoints for both DynamoDB and SM.
    monkeypatch.delenv("WITNESS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("WITNESS_SECRET_ENDPOINT_URL", raising=False)


def _reset_singletons(witness_handler):
    witness_handler._hby = witness_handler._hab = witness_handler._parser = None


@needs_moto
def test_destroy_replace_reproduces_same_aid(monkeypatch):
    import witness_handler
    with mock_aws():
        _set_env(monkeypatch)
        _create_baser_table()

        # --- Cold start: get-or-create keeper secret + incept ---
        _reset_singletons(witness_handler)
        witness_handler.init()
        pre1 = witness_handler._hab.pre
        assert pre1 and pre1.startswith(("B", "D"))  # non-transferable witness

        # The keeper secret now exists with salt + a >=21-char bran + a
        # non-null keeper blob (incept populated + auto-flushed the keystore).
        doc = _read_keeper_secret()
        assert isinstance(doc["salt"], str) and doc["salt"]
        assert isinstance(doc["bran"], str) and len(doc["bran"]) >= 21
        assert doc["keeper"] is not None

        # --- Simulate destroy-replace: wipe the Baser table EMPTY, keep the
        # keeper secret untouched (CloudFormation destroys the -db table on a
        # destroy-replace; the SM secret survives). ---
        _drop_baser_table()
        _create_baser_table()
        salt_before = _read_keeper_secret()["salt"]

        _reset_singletons(witness_handler)
        witness_handler.init()
        pre2 = witness_handler._hab.pre

        # The salt was preserved across the wipe...
        assert _read_keeper_secret()["salt"] == salt_before
        # ...so re-inception reproduces the SAME witness AID. This is the
        # assertion that proves destroy-replace safety.
        assert pre2 == pre1


@needs_moto
def test_keeper_is_encrypted(monkeypatch):
    import witness_handler
    with mock_aws():
        _set_env(monkeypatch)
        _create_baser_table()

        _reset_singletons(witness_handler)
        witness_handler.init()

        # bran engaged aeid: the keeper is ENCRYPTED for the first time.
        assert witness_handler._hby.ks.gbls.get("aeid") is not None

        # The keeper's bran is the 21-char value carried in the secret.
        ks = witness_handler._hby.ks
        assert len(ks.bran) == 21
        assert ks.bran == _read_keeper_secret()["bran"]
