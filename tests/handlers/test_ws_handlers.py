"""Unit tests for ws_handlers — WebSocket connect/disconnect/subscribe.

Follows the same patterns as test_mailbox_handler.py:
- moto @mock_aws for DynamoDB registry table operations
- real temp Habery + makeHab for genuine signed qry construction
- autouse mock_init fixture to skip the heavy cold-start init()

TDD order: tests written first (RED), then ws_handlers.py (GREEN).

Native-parity contract (§5.3 DECISION REVISION 2026-06-30):
  Subscribe gate = "pre in kevers" only — mirrors keripy Kevery.processQuery.
  No signature verification, no signer↔owner binding.  A qry for a KNOWN AID
  signed by a DIFFERENT key MUST be accepted (see
  test_subscribe_known_aid_different_signer_is_accepted below).
"""

import json
import os
from unittest.mock import patch, MagicMock

import boto3
import pytest
from moto import mock_aws

from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import eventing


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WS_CONN_TABLE = "test-ws-conn-registry"


@pytest.fixture(autouse=True)
def ws_env(monkeypatch):
    """Set required env vars for all WS handler tests."""
    monkeypatch.setenv("WS_CONN_TABLE", WS_CONN_TABLE)


@pytest.fixture(autouse=True)
def mock_ws_init(monkeypatch):
    """Skip mailbox_handler.init() in WS handler unit tests."""
    monkeypatch.setattr("mailbox_handler.init", lambda: None)
    yield


@pytest.fixture
def conn_table():
    """Create the registry DynamoDB table via moto."""
    with mock_aws():
        client = boto3.resource("dynamodb", region_name="us-east-1")
        table = client.create_table(
            TableName=WS_CONN_TABLE,
            KeySchema=[{"AttributeName": "connectionId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "connectionId", "AttributeType": "S"},
                {"AttributeName": "pre", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "byPre",
                "KeySchema": [{"AttributeName": "pre", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield table


def _make_ws_event(connection_id, body=None):
    """Build a minimal API Gateway WebSocket Lambda event."""
    event = {
        "requestContext": {"connectionId": connection_id},
    }
    if body is not None:
        event["body"] = body
    return event


def _make_signed_mbx_qry(sender_hab, recipient_pre, topics):
    """Build a genuine signed qry r=/mbx as bytes using hab.query().

    Uses hab.query() which calls eventing.query(...) + endorse(last=True),
    producing a TransLastIdxSig-signed message the parser can verify.
    """
    qry_msg = sender_hab.query(
        pre=recipient_pre,
        src=sender_hab.pre,
        route="/mbx",
        query={"topics": topics},
    )
    return bytes(qry_msg)


# ---------------------------------------------------------------------------
# $connect tests
# ---------------------------------------------------------------------------

def test_connect_returns_200():
    """$connect: lightweight accept — no DB, no init(), always 200."""
    from ws_handlers import connect
    event = _make_ws_event("abc-connection-123")
    result = connect(event, {})
    assert result == {"statusCode": 200}


def test_connect_no_registry_write(conn_table):
    """$connect must not write any row to the registry table."""
    from ws_handlers import connect
    event = _make_ws_event("abc-connection-123")
    connect(event, {})
    # Table should be empty — no rows created
    resp = conn_table.scan()
    assert resp["Count"] == 0


# ---------------------------------------------------------------------------
# $disconnect tests
# ---------------------------------------------------------------------------

def test_disconnect_deletes_existing_row(conn_table, monkeypatch):
    """$disconnect: DeleteItem removes an existing registry row."""
    conn_id = "disco-conn-001"
    conn_table.put_item(Item={"connectionId": conn_id, "pre": "BFakeAID", "topics": {}})

    # conn_table fixture already runs inside mock_aws; call disconnect within
    # the same moto context (no nested mock_aws block — that would create a
    # separate isolated context and the table would not be visible).
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from ws_handlers import disconnect
    event = _make_ws_event(conn_id)
    result = disconnect(event, {})

    assert result == {"statusCode": 200}
    resp = conn_table.get_item(Key={"connectionId": conn_id})
    assert "Item" not in resp


def test_disconnect_idempotent_on_missing_row(conn_table, monkeypatch):
    """$disconnect: DeleteItem on an absent row must not raise — idempotent."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from ws_handlers import disconnect
    event = _make_ws_event("does-not-exist-conn")
    # Must not raise
    result = disconnect(event, {})
    assert result == {"statusCode": 200}


# ---------------------------------------------------------------------------
# subscribe ($default / action=subscribe) tests
# ---------------------------------------------------------------------------

@mock_aws
def test_subscribe_valid_signed_qry_writes_registry_row(monkeypatch):
    """subscribe: a valid signed qry whose AID is known → exactly one row."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    # Build a real Habery so the subscriber AID's kever is known.
    hby = Habery(name="sub-test", temp=True, salt=Salter().qb64)
    subscriber = hby.makeHab(name="alice", transferable=True)
    recipient_pre = subscriber.pre  # subscribing for own mailbox (common case)
    topics = {"receipt": 0, "/credential": 5}

    qry_bytes = _make_signed_mbx_qry(subscriber, recipient_pre, topics)
    import base64
    qry_qb64 = base64.b64encode(qry_bytes).decode()

    # Provision DynamoDB table
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.create_table(
        TableName=WS_CONN_TABLE,
        KeySchema=[{"AttributeName": "connectionId", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "connectionId", "AttributeType": "S"},
            {"AttributeName": "pre", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "byPre",
            "KeySchema": [{"AttributeName": "pre", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()

    conn_id = "conn-valid-001"
    body = json.dumps({"action": "subscribe", "qry": qry_qb64})
    event = _make_ws_event(conn_id, body=body)

    # Patch mailbox_handler globals that default() reads at call time.
    with patch("mailbox_handler._hby", hby), \
         patch("mailbox_handler._initialized", True), \
         patch("mailbox_handler.init", lambda: None):
        from ws_handlers import default
        result = default(event, {})

    assert result["statusCode"] == 200, f"Expected 200, got {result}"

    item = table.get_item(Key={"connectionId": conn_id}).get("Item")
    assert item is not None, "Registry row must be written on valid subscribe"
    assert item["pre"] == recipient_pre
    # DynamoDB returns numeric attributes as Decimal; int() accepts Decimal
    assert int(item.get("expireAt", 0)) > 0

    hby.close()


@mock_aws
def test_subscribe_unknown_aid_does_not_write_row(monkeypatch):
    """subscribe: a qry signed by an AID NOT in _hby.kevers → no row, error.

    Native-parity gate: "pre not in kevers" is the only rejection criterion.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    # Build two Haberies: signer unknown to server
    unknown_hby = Habery(name="unknown-test", temp=True, salt=Salter().qb64)
    signer = unknown_hby.makeHab(name="bob", transferable=True)
    recipient_pre = signer.pre
    qry_bytes = _make_signed_mbx_qry(signer, recipient_pre, {"receipt": 0})
    import base64
    qry_qb64 = base64.b64encode(qry_bytes).decode()

    # Server Habery holds NO kevers for this AID — genuinely empty
    server_hby = Habery(name="server-test", temp=True, salt=Salter().qb64)

    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.create_table(
        TableName=WS_CONN_TABLE,
        KeySchema=[{"AttributeName": "connectionId", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "connectionId", "AttributeType": "S"},
            {"AttributeName": "pre", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "byPre",
            "KeySchema": [{"AttributeName": "pre", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()

    conn_id = "conn-unknown-002"
    body = json.dumps({"action": "subscribe", "qry": qry_qb64})
    event = _make_ws_event(conn_id, body=body)

    with patch("mailbox_handler._hby", server_hby), \
         patch("mailbox_handler._initialized", True), \
         patch("mailbox_handler.init", lambda: None):
        from ws_handlers import default
        result = default(event, {})

    # Must reject — error status, no registry row
    assert result["statusCode"] >= 400, (
        f"Expected 4xx for unknown AID, got {result['statusCode']}"
    )
    item = table.get_item(Key={"connectionId": conn_id}).get("Item")
    assert item is None, "No registry row must be written for unknown AID"

    unknown_hby.close()
    server_hby.close()


@mock_aws
def test_subscribe_known_aid_different_signer_is_accepted(monkeypatch):
    """subscribe: a qry for a KNOWN recipient AID, signed by a DIFFERENT key, MUST be accepted.

    This test explicitly locks in the §5.3 DECISION REVISION (2026-06-30):
    native parity means NO signer↔owner binding.  keripy's Kevery.processQuery
    accepts any structurally valid /mbx qry whose q["i"] is in kevers — it does
    not check that the signer == recipient.  The serverless WS subscribe applies
    that SAME check and nothing more.

    A future change that re-adds a signature or signer-binding gate WILL break
    this test, making the regression visible.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    # Two AIDs in the server's kevers: recipient (alice) and a different signer (bob).
    server_hby = Habery(name="diff-signer-test", temp=True, salt=Salter().qb64)
    alice = server_hby.makeHab(name="alice", transferable=True)  # recipient
    bob = server_hby.makeHab(name="bob", transferable=True)      # signer ≠ recipient

    recipient_pre = alice.pre
    topics = {"receipt": 0}

    # bob signs a qry addressed to alice's mailbox (bob.pre != alice.pre)
    qry_bytes = _make_signed_mbx_qry(bob, recipient_pre, topics)
    import base64
    qry_qb64 = base64.b64encode(qry_bytes).decode()

    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.create_table(
        TableName=WS_CONN_TABLE,
        KeySchema=[{"AttributeName": "connectionId", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "connectionId", "AttributeType": "S"},
            {"AttributeName": "pre", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "byPre",
            "KeySchema": [{"AttributeName": "pre", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()

    conn_id = "conn-diff-signer-005"
    body = json.dumps({"action": "subscribe", "qry": qry_qb64})
    event = _make_ws_event(conn_id, body=body)

    with patch("mailbox_handler._hby", server_hby), \
         patch("mailbox_handler._initialized", True), \
         patch("mailbox_handler.init", lambda: None):
        from ws_handlers import default
        result = default(event, {})

    # MUST be accepted — native parity does not bind signer to recipient
    assert result["statusCode"] == 200, (
        f"Native parity: a qry for a KNOWN AID signed by a different key MUST be "
        f"accepted (mirrors keripy Kevery.processQuery). Got {result}"
    )
    item = table.get_item(Key={"connectionId": conn_id}).get("Item")
    assert item is not None, "Registry row must be written (native parity: no signer binding)"
    assert item["pre"] == recipient_pre

    server_hby.close()


@mock_aws
def test_subscribe_unknown_action_returns_400(monkeypatch):
    """subscribe via $default: unrecognized action → 400, no row."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    from ws_handlers import default
    event = _make_ws_event("conn-badact-003",
                           body=json.dumps({"action": "unsubscribe"}))
    result = default(event, {})
    assert result["statusCode"] == 400


@mock_aws
def test_subscribe_non_mbx_qry_returns_error(monkeypatch):
    """subscribe: a qry with r != /mbx must not register."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    hby = Habery(name="nmqry-test", temp=True, salt=Salter().qb64)
    hab = hby.makeHab(name="carol", transferable=False)

    # Build a qry with a different route (e.g. /ksn)
    qry_serder = eventing.query(route="/ksn", query={"i": hab.pre, "src": hab.pre})
    qry_bytes = bytes(hab.endorse(qry_serder, last=True, framed=False))
    import base64
    qry_qb64 = base64.b64encode(qry_bytes).decode()

    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.create_table(
        TableName=WS_CONN_TABLE,
        KeySchema=[{"AttributeName": "connectionId", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "connectionId", "AttributeType": "S"},
            {"AttributeName": "pre", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "byPre",
            "KeySchema": [{"AttributeName": "pre", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()

    conn_id = "conn-nonmbx-004"
    body = json.dumps({"action": "subscribe", "qry": qry_qb64})
    event = _make_ws_event(conn_id, body=body)

    with patch("mailbox_handler._hby", hby), \
         patch("mailbox_handler._initialized", True), \
         patch("mailbox_handler.init", lambda: None):
        from ws_handlers import default
        result = default(event, {})

    assert result["statusCode"] >= 400
    item = table.get_item(Key={"connectionId": conn_id}).get("Item")
    assert item is None

    hby.close()
