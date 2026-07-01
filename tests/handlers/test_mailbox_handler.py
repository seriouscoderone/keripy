"""Unit tests for mailbox_handler — no AWS, no DynamoDB."""

import json
from unittest.mock import patch, MagicMock

import falcon
import pytest
from falcon import testing

from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import eventing


@pytest.fixture(autouse=True)
def mock_init(monkeypatch):
    """Skip real init() in unit tests — they patch _hab/_hby directly."""
    monkeypatch.setattr("mailbox_handler.init", lambda: None)
    yield


def test_build_app_returns_falcon_asgi_app():
    """build_app() returns a Falcon ASGI App instance."""
    from mailbox_handler import build_app
    app = build_app()
    assert isinstance(app, falcon.asgi.App)


def test_get_status_returns_mailbox_aid():
    """GET / returns status dict with mailbox AID."""
    from mailbox_handler import build_app
    with patch("mailbox_handler._hab") as mock_hab, \
         patch("mailbox_handler._hby") as mock_hby:
        mock_hab.pre = "BFake_mailbox_AID_for_test_only_"
        mock_hab.name = "mailbox"
        mock_hab.kever.sn = 0
        mock_hby.kevers = {"BFake_mailbox_AID_for_test_only_": object()}
        client = testing.TestClient(build_app())
        result = client.simulate_get("/")
    assert result.status_code == 200
    assert result.json["mailbox"] == "BFake_mailbox_AID_for_test_only_"
    assert result.json["alias"] == "mailbox"
    assert result.json["sn"] == 0
    assert result.json["kevers"] == 1


def test_get_unknown_route_returns_404():
    """Falcon's default 404 handler returns 404 for unknown routes."""
    from mailbox_handler import build_app
    client = testing.TestClient(build_app())
    result = client.simulate_get("/does-not-exist")
    assert result.status_code == 404


# ---------------------------------------------------------------------------
# Task 2.2: get_body_bytes and _extract_cesr_stream helpers
# ---------------------------------------------------------------------------

def test_get_body_bytes_plain_string():
    from mailbox_handler import get_body_bytes
    event = {"body": "hello"}
    assert get_body_bytes(event) == b"hello"


def test_get_body_bytes_base64_encoded():
    from mailbox_handler import get_body_bytes
    import base64
    event = {"body": base64.b64encode(b"hello").decode(), "isBase64Encoded": True}
    assert get_body_bytes(event) == b"hello"


def test_get_body_bytes_empty():
    from mailbox_handler import get_body_bytes
    assert get_body_bytes({"body": ""}) == b""
    assert get_body_bytes({}) == b""


def test_extract_cesr_stream_body_only():
    from mailbox_handler import _extract_cesr_stream
    event = {"body": "EVENT_CESR", "headers": {}}
    assert bytes(_extract_cesr_stream(event)) == b"EVENT_CESR"


def test_extract_cesr_stream_with_attachment_header():
    from mailbox_handler import _extract_cesr_stream
    event = {"body": "EVENT", "headers": {"CESR-ATTACHMENT": "-AABATTACH"}}
    # No -V/-C wrapper, attachment passes through unchanged
    assert bytes(_extract_cesr_stream(event)) == b"EVENT-AABATTACH"


def test_extract_cesr_stream_case_insensitive_header():
    from mailbox_handler import _extract_cesr_stream
    event = {"body": "EVENT", "headers": {"cesr-attachment": "-AABSIG"}}
    assert bytes(_extract_cesr_stream(event)) == b"EVENT-AABSIG"


# ---------------------------------------------------------------------------
# Task 2.3: _detect_mbx_query helper
# ---------------------------------------------------------------------------

def test_detect_mbx_query_returns_none_for_malformed():
    from mailbox_handler import _detect_mbx_query
    assert _detect_mbx_query(b"not a serder") is None
    assert _detect_mbx_query(b"") is None


def test_detect_mbx_query_returns_none_for_non_qry():
    """An icp event should not be detected as an mbx query."""
    from mailbox_handler import _detect_mbx_query
    hby = Habery(name="t", temp=True, salt=Salter().qb64)
    hab = hby.makeHab(name="alice", transferable=False)
    icp_msg = hab.msgOwnEvent(sn=0)
    # Extract just the serder portion (before -AAB attachments)
    icp_serder_bytes = icp_msg.split(b"-A", 1)[0]
    try:
        assert _detect_mbx_query(icp_serder_bytes) is None
    finally:
        hby.close()


def test_detect_mbx_query_returns_serder_for_mbx_qry():
    """A qry serder with r=/mbx should be detected."""
    from mailbox_handler import _detect_mbx_query
    # Construct a minimal qry serder ourselves
    qry_serder = eventing.query(
        route="/mbx",
        query={"pre": "BFake_recipient", "topics": {"receipt": 0}}
    )
    assert _detect_mbx_query(qry_serder.raw) is not None
    assert _detect_mbx_query(qry_serder.raw).ked["r"] in ("/mbx", "mbx")


# ---------------------------------------------------------------------------
# Task 2.4: _format_sse_events helper
# ---------------------------------------------------------------------------

def test_format_sse_events_empty_topics_returns_empty():
    from mailbox_handler import _format_sse_events
    hby = MagicMock()
    hby.db.cloneTopicIter = MagicMock(return_value=iter([]))
    out = _format_sse_events(hby, "BFake_recipient", {"receipt": 0})
    assert out == ""


def test_format_sse_events_emits_sse_frame_per_message():
    from mailbox_handler import _format_sse_events
    hby = MagicMock()
    hby.db.cloneTopicIter = MagicMock(
        return_value=iter([(0, b"topic1", b"message-one"),
                          (1, b"topic1", b"message-two")])
    )
    out = _format_sse_events(hby, "BFake_recipient", {"credential": 0})
    # Two events emitted
    assert out.count("data: ") == 2
    assert "id: 0" in out
    assert "id: 1" in out
    assert "event: credential" in out
    assert "retry: 1000" in out
    assert "message-one" in out
    assert "message-two" in out


def test_format_sse_events_topic_key_construction():
    """Topic key is f'{pre}/{name}'.encode() — matches forwarding.py:500."""
    from mailbox_handler import _format_sse_events
    hby = MagicMock()
    captured = {}
    def fake_iter(topic, fn):
        captured["topic"] = topic
        captured["fn"] = fn
        return iter([])
    hby.db.cloneTopicIter = fake_iter
    _format_sse_events(hby, "BAlice", {"credential": 5})
    assert captured["topic"] == b"BAlice/credential"
    assert captured["fn"] == 6  # last_on + 1


# ---------------------------------------------------------------------------
# Task 2.5: OOBIResource
# ---------------------------------------------------------------------------

def test_oobi_returns_404_for_non_mailbox_aid():
    from mailbox_handler import build_app
    with patch("mailbox_handler._hab") as mock_hab, \
         patch("mailbox_handler._hby") as mock_hby:
        mock_hab.pre = "BMailbox_AID"
        mock_hby.kevers = {"BMailbox_AID": MagicMock()}
        client = testing.TestClient(build_app())
        result = client.simulate_get("/oobi/BSome_other_AID/mailbox")
    assert result.status_code == 404


def test_oobi_returns_cesr_for_mailbox_self():
    """OOBI for the mailbox's own AID returns CESR with KERI-AID header."""
    from mailbox_handler import build_app
    fake_msgs = b'{"v":"KERI10JSON","t":"icp"}-AAB-DUMMY-CESR-BYTES'
    with patch("mailbox_handler._hab") as mock_hab, \
         patch("mailbox_handler._hby") as mock_hby:
        mock_hab.pre = "BMailbox_AID"
        mock_hab.replyToOobi = MagicMock(return_value=bytearray(fake_msgs))
        mock_hab.replay = MagicMock(return_value=bytearray())
        mock_hby.prefixes = {"BMailbox_AID"}
        kever = MagicMock()
        kever.wits = []
        mock_hby.kevers = {"BMailbox_AID": kever}
        mock_hby.db.fullyWitnessed = MagicMock(return_value=True)
        client = testing.TestClient(build_app())
        result = client.simulate_get("/oobi/BMailbox_AID/mailbox")
    assert result.status_code == 200
    assert result.headers.get("Content-Type") == "application/cesr"
    assert result.headers.get("KERI-AID") == "BMailbox_AID"


def test_oobi_bare_path_defaults_to_self():
    """GET /oobi (no aid in path) returns the mailbox's own OOBI."""
    from mailbox_handler import build_app
    fake_msgs = b'OOBI-CESR-BYTES'
    with patch("mailbox_handler._hab") as mock_hab, \
         patch("mailbox_handler._hby") as mock_hby:
        mock_hab.pre = "BMailbox_AID"
        mock_hab.replyToOobi = MagicMock(return_value=bytearray(fake_msgs))
        mock_hab.replay = MagicMock(return_value=bytearray())
        mock_hby.prefixes = {"BMailbox_AID"}
        kever = MagicMock()
        kever.wits = []
        mock_hby.kevers = {"BMailbox_AID": kever}
        mock_hby.db.fullyWitnessed = MagicMock(return_value=True)
        client = testing.TestClient(build_app())
        result = client.simulate_get("/oobi")
    assert result.status_code == 200


# ---------------------------------------------------------------------------
# Task 2.6: IngestResource (POST / and PUT /)
# ---------------------------------------------------------------------------

def test_ingest_empty_body_returns_400():
    """POST / with empty body returns 400."""
    from mailbox_handler import build_app
    with patch("mailbox_handler._hby") as mock_hby:
        mock_hby.psr.parse = MagicMock()
        mock_hby.kvy.processEscrows = MagicMock()
        client = testing.TestClient(build_app())
        result = client.simulate_post("/", body="")
    assert result.status_code == 400


def test_ingest_deposit_returns_204():
    """A /fwd exn (no mbx query) returns 204 — empty body, no Content-Type."""
    from mailbox_handler import build_app
    with patch("mailbox_handler._hby") as mock_hby, \
         patch("mailbox_handler._detect_mbx_query", return_value=None):
        mock_hby.psr.parse = MagicMock()
        mock_hby.kvy.processEscrows = MagicMock()
        client = testing.TestClient(build_app())
        result = client.simulate_post("/", body=b"FAKE_CESR",
                                     headers={"Content-Type": "application/cesr"})
    assert result.status_code == 204


def test_ingest_mbx_qry_returns_sse_streamed():
    """A qry r=/mbx returns 200 + Content-Type: text/event-stream (streaming path).

    Falcon TestClient drains the async generator into result.text.
    """
    from mailbox_handler import build_app
    fake_serder = MagicMock()
    fake_serder.ked = {
        "t": "qry", "r": "/mbx",
        "q": {"pre": "BRecipient", "topics": {"receipt": 0}}
    }
    sse_chunk = b"id: 0\nevent: receipt\nretry: 1000\ndata: msg\n\n"

    async def fake_stream(pre, topics):
        yield sse_chunk

    with patch("mailbox_handler._hby") as mock_hby, \
         patch("mailbox_handler._detect_mbx_query", return_value=fake_serder), \
         patch("mailbox_handler._stream_mbx_response", side_effect=fake_stream):
        mock_hby.psr.parse = MagicMock()
        mock_hby.kvy.processEscrows = MagicMock()
        client = testing.TestClient(build_app())
        result = client.simulate_post("/", body=b"FAKE_CESR",
                                     headers={"Content-Type": "application/cesr"})
    assert result.status_code == 200
    assert result.headers.get("Content-Type") == "text/event-stream"
    assert "data: msg" in result.text


def test_ingest_mbx_qry_canonical_i_key_drains_correctly():
    """qry r=/mbx carrying only q['i'] (canonical keripy habbing.py:1565) returns
    200 + event-stream with messages — proves Change B fixes canonical clients.

    Before the fix (q.get('pre') only), q['i'] without q['pre'] → 400.
    """
    from mailbox_handler import build_app
    fake_serder = MagicMock()
    fake_serder.ked = {
        "t": "qry", "r": "/mbx",
        "q": {"i": "BCanonical", "topics": {"receipt": 0}}  # canonical key, no "pre"
    }
    sse_chunk = b"id: 0\nevent: receipt\nretry: 1000\ndata: canon-msg\n\n"

    async def fake_stream(pre, topics):
        assert pre == "BCanonical", f"expected BCanonical, got {pre!r}"
        yield sse_chunk

    with patch("mailbox_handler._hby") as mock_hby, \
         patch("mailbox_handler._detect_mbx_query", return_value=fake_serder), \
         patch("mailbox_handler._stream_mbx_response", side_effect=fake_stream):
        mock_hby.psr.parse = MagicMock()
        mock_hby.kvy.processEscrows = MagicMock()
        client = testing.TestClient(build_app())
        result = client.simulate_post("/", body=b"FAKE_CESR",
                                     headers={"Content-Type": "application/cesr"})
    assert result.status_code == 200
    assert result.headers.get("Content-Type") == "text/event-stream"
    assert "data: canon-msg" in result.text


def test_ingest_mbx_qry_missing_pre_returns_400():
    """qry r=/mbx without q.pre returns 400."""
    from mailbox_handler import build_app
    fake_serder = MagicMock()
    fake_serder.ked = {"t": "qry", "r": "/mbx", "q": {"topics": {"receipt": 0}}}
    with patch("mailbox_handler._hby") as mock_hby, \
         patch("mailbox_handler._detect_mbx_query", return_value=fake_serder):
        mock_hby.psr.parse = MagicMock()
        mock_hby.kvy.processEscrows = MagicMock()
        client = testing.TestClient(build_app())
        result = client.simulate_post("/", body=b"FAKE_CESR",
                                     headers={"Content-Type": "application/cesr"})
    assert result.status_code == 400


def test_put_root_also_ingests():
    """PUT / dispatches to the same ingest path as POST /."""
    from mailbox_handler import build_app
    with patch("mailbox_handler._hby") as mock_hby, \
         patch("mailbox_handler._detect_mbx_query", return_value=None):
        mock_hby.psr.parse = MagicMock()
        mock_hby.kvy.processEscrows = MagicMock()
        client = testing.TestClient(build_app())
        result = client.simulate_put("/", body=b"FAKE_CESR",
                                    headers={"Content-Type": "application/cesr"})
    assert result.status_code == 204


# ---------------------------------------------------------------------------
# Task 3: _stream_mbx_response async generator (drain-only, replaces long-poll)
# ---------------------------------------------------------------------------

import asyncio as _asyncio_for_tests  # noqa: F401 — kept for pytest-asyncio compat


@pytest.mark.asyncio
async def test_stream_mbx_response_yields_initial_drain():
    """Drain yields SSE frames for all queued messages then completes."""
    from mailbox_handler import _stream_mbx_response
    with patch("mailbox_handler._hby") as mock_hby:
        mock_hby.db.cloneTopicIter = MagicMock(return_value=iter([
            (0, b"BRecipient/receipt", b"msg-one"),
            (1, b"BRecipient/receipt", b"msg-two"),
        ]))
        gen = _stream_mbx_response("BRecipient", {"receipt": 0})
        frames = []
        async for frame in gen:
            frames.append(frame)
    body = b"".join(frames)
    assert b"data: msg-one" in body
    assert b"data: msg-two" in body
    assert b"id: 0" in body
    assert b"id: 1" in body


@pytest.mark.asyncio
async def test_stream_mbx_response_drain_only_no_keepalive():
    """Drain-only generator emits NO :keepalive frame and completes after one pass."""
    from mailbox_handler import _stream_mbx_response
    with patch("mailbox_handler._hby") as mock_hby:
        mock_hby.db.cloneTopicIter = MagicMock(return_value=iter([]))
        gen = _stream_mbx_response("BRecipient", {"receipt": 0})
        frames = []
        async for frame in gen:
            frames.append(frame)
    assert not any(b":keepalive" in f for f in frames), \
        f"drain-only must not emit keepalive frames, got: {frames}"
    # Generator must complete (StopAsyncIteration) rather than hold open
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


@pytest.mark.asyncio
async def test_stream_mbx_response_drain_terminates_no_repolling():
    """Drain-only generator returns backlog then stops — does NOT re-poll.

    TDD: this would hang/keepalive under the old held-open generator.
    Under drain-only it must complete (StopAsyncIteration) immediately.
    """
    from mailbox_handler import _stream_mbx_response
    poll_count = {"n": 0}

    def counting_iter(topic, fn):
        poll_count["n"] += 1
        if poll_count["n"] == 1:
            return iter([(3, topic, b"backlog-msg")])
        # A second poll would mean the old behavior leaked through
        return iter([(99, topic, b"should-not-appear")])

    with patch("mailbox_handler._hby") as mock_hby:
        mock_hby.db.cloneTopicIter = counting_iter
        gen = _stream_mbx_response("BRecipient", {"receipt": 0})
        frames = []
        async for frame in gen:
            frames.append(frame)

    body = b"".join(frames)
    assert b"data: backlog-msg" in body
    assert b"should-not-appear" not in body, "drain-only must not re-poll"
    assert poll_count["n"] == 1, f"cloneTopicIter polled {poll_count['n']} times; expected 1"


# ---------------------------------------------------------------------------
# Task 2.8 / Task 8: init() cold-start on SecretKeeper — keeper-secret model.
#
# These two tests run the REAL init() under moto (mock_aws mocks BOTH DynamoDB
# and Secrets Manager in-process). They override the autouse `mock_init`
# fixture (which patches out init() for the unit tests above) by undoing the
# monkeypatch and resetting the module singletons INCLUDING `_initialized`
# (otherwise the `if _initialized: return` guard at the top of init() makes the
# second call vacuous and the destroy-replace assertion meaningless).
# ---------------------------------------------------------------------------

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")

_MB_REGION = "us-east-1"
_MB_BASER_TABLE = "mailbox-test-db"
_MB_KEEPER_SECRET = "keri/mailbox-test/keeper"


def _mb_create_baser_table():
    """Create the mailbox Baser DynamoDB table mirroring template.yaml's
    PK/SK + subdb-index GSI schema. (No keeper table — that's gone.)"""
    import boto3
    client = boto3.client("dynamodb", region_name=_MB_REGION)
    client.create_table(
        TableName=_MB_BASER_TABLE,
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
    client.get_waiter("table_exists").wait(TableName=_MB_BASER_TABLE)


def _mb_drop_baser_table():
    import boto3
    client = boto3.client("dynamodb", region_name=_MB_REGION)
    client.delete_table(TableName=_MB_BASER_TABLE)
    client.get_waiter("table_not_exists").wait(TableName=_MB_BASER_TABLE)


def _mb_read_keeper_secret():
    import boto3
    resp = boto3.client("secretsmanager", region_name=_MB_REGION
                        ).get_secret_value(SecretId=_MB_KEEPER_SECRET)
    return json.loads(resp["SecretString"])


def _mb_set_env(monkeypatch):
    monkeypatch.undo()   # drop the autouse mock_init so the REAL init() runs
    monkeypatch.setenv("MAILBOX_NAME", "mailbox-test")
    monkeypatch.setenv("MAILBOX_ALIAS", "mailbox")
    monkeypatch.setenv("MAILBOX_REGION", _MB_REGION)
    monkeypatch.setenv("MAILBOX_URL", "")          # skip self-endpoint publish
    monkeypatch.setenv("MAILBOX_KEEPER_SECRET", _MB_KEEPER_SECRET)
    monkeypatch.setenv("MAILBOX_BASER_TABLE", _MB_BASER_TABLE)
    # Leave MAILBOX_ENDPOINT_URL / MAILBOX_SECRET_ENDPOINT_URL UNSET so moto
    # intercepts the default AWS endpoints for both DynamoDB and SM.
    monkeypatch.delenv("MAILBOX_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("MAILBOX_SECRET_ENDPOINT_URL", raising=False)


def _mb_reset_singletons(mailbox_handler):
    # Reset _initialized too — otherwise the `if _initialized: return` guard
    # short-circuits the second init() and the redeploy test is vacuous.
    mailbox_handler._hby = mailbox_handler._hab = mailbox_handler._parser = None
    mailbox_handler._initialized = False


@needs_moto
def test_destroy_replace_reproduces_same_aid(monkeypatch):
    import mailbox_handler
    with mock_aws():
        _mb_set_env(monkeypatch)
        _mb_create_baser_table()

        # --- Cold start: get-or-create keeper secret + incept ---
        _mb_reset_singletons(mailbox_handler)
        mailbox_handler.init()
        pre1 = mailbox_handler._hab.pre
        assert pre1 and pre1.startswith(("B", "D"))  # non-transferable mailbox

        # The keeper secret now exists with salt + a >=21-char bran + a
        # non-null keeper blob (incept populated + auto-flushed the keystore).
        doc = _mb_read_keeper_secret()
        assert isinstance(doc["salt"], str) and doc["salt"]
        assert isinstance(doc["bran"], str) and len(doc["bran"]) >= 21
        assert doc["keeper"] is not None

        # --- Simulate destroy-replace: wipe the Baser table EMPTY, keep the
        # keeper secret untouched (CloudFormation destroys the -db table on a
        # destroy-replace; the SM secret survives). ---
        _mb_drop_baser_table()
        _mb_create_baser_table()
        salt_before = _mb_read_keeper_secret()["salt"]

        _mb_reset_singletons(mailbox_handler)
        mailbox_handler.init()
        pre2 = mailbox_handler._hab.pre

        # The salt was preserved across the wipe...
        assert _mb_read_keeper_secret()["salt"] == salt_before
        # ...so re-inception reproduces the SAME mailbox AID. This is the
        # assertion that proves destroy-replace safety.
        assert pre2 == pre1


@needs_moto
def test_keeper_is_encrypted(monkeypatch):
    import mailbox_handler
    with mock_aws():
        _mb_set_env(monkeypatch)
        _mb_create_baser_table()

        _mb_reset_singletons(mailbox_handler)
        mailbox_handler.init()

        # bran engaged aeid: the keeper is ENCRYPTED for the first time.
        assert mailbox_handler._hby.ks.gbls.get("aeid") is not None

        # The keeper's bran is the 21-char value carried in the secret.
        ks = mailbox_handler._hby.ks
        assert len(ks.bran) == 21
        assert ks.bran == _mb_read_keeper_secret()["bran"]


# ---------------------------------------------------------------------------
# Task 4: _detect_fwd + _notify_subscribers + nudge fan-out on /fwd deposit
# ---------------------------------------------------------------------------

_WS_CONN_TABLE = "test-ws-conn-registry-nudge"
_WS_CALLBACK_URL = "https://fake-ws.execute-api.us-east-1.amazonaws.com/prod"


def _make_fwd_serder(pre="Ebob", topic="credential"):
    """Build a minimal /fwd exn serder (unsigned, for peek-only tests).

    Uses keri.core.eventing.exchange which places modifiers in the 'q' field,
    matching the /fwd exn structure that _detect_fwd reads from serder.ked["q"].
    """
    from keri.core.eventing import exchange
    fwd_serder = exchange(
        route="/fwd",
        sender="BAlice_fake_sender_for_test",
        modifiers={"pre": pre, "topic": topic},
    )
    return fwd_serder


@pytest.fixture
def nudge_conn_table(monkeypatch):
    """Create the WS registry table + set env vars for nudge tests."""
    monkeypatch.setenv("WS_CONN_TABLE", _WS_CONN_TABLE)
    monkeypatch.setenv("WS_CALLBACK_URL", _WS_CALLBACK_URL)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        import boto3
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName=_WS_CONN_TABLE,
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


# --- _detect_fwd unit tests -------------------------------------------------

def test_detect_fwd_returns_none_for_malformed():
    from mailbox_handler import _detect_fwd
    assert _detect_fwd(b"not a serder") is None
    assert _detect_fwd(b"") is None


def test_detect_fwd_returns_none_for_non_fwd():
    """A /mbx qry must NOT be detected as a /fwd exn."""
    from mailbox_handler import _detect_fwd
    qry_serder = eventing.query(
        route="/mbx",
        query={"pre": "BFake_recipient", "topics": {"receipt": 0}}
    )
    assert _detect_fwd(qry_serder.raw) is None


def test_detect_fwd_returns_serder_for_fwd_exn():
    """A /fwd exn serder must be detected and returned."""
    from mailbox_handler import _detect_fwd
    fwd_serder = _make_fwd_serder(pre="Ebob", topic="credential")
    result = _detect_fwd(fwd_serder.raw)
    assert result is not None
    assert result.ked["t"] == "exn"
    assert result.ked["r"] == "/fwd"


# --- nudge fire-and-forget + 204 never blocked tests ------------------------

@needs_moto
def test_fwd_deposit_sends_nudge_and_returns_204(nudge_conn_table, monkeypatch):
    """A live registered connection for Ebob + a /fwd deposit → nudge sent + 204.

    The nudge body must JSON-decode to {"type":"mailbox.nudge","pre":"Ebob",
    "topic":"credential",...}.
    """
    from mailbox_handler import build_app

    # Register a live connection for Ebob in the DynamoDB registry.
    nudge_conn_table.put_item(Item={
        "connectionId": "conn-abc-123",
        "pre": "Ebob",
        "topics": {"credential": 0},
        "connectedAt": 0,
        "expireAt": 9999999999,
    })

    fwd_serder = _make_fwd_serder(pre="Ebob", topic="credential")

    sent_calls = []

    def fake_post_to_connection(**kwargs):
        sent_calls.append(kwargs)

    fake_apigw = MagicMock()
    fake_apigw.post_to_connection.side_effect = fake_post_to_connection

    with patch("mailbox_handler._hby") as mock_hby, \
         patch("mailbox_handler._detect_mbx_query", return_value=None), \
         patch("boto3.client", return_value=fake_apigw):
        mock_hby.psr.parse = MagicMock()
        mock_hby.kvy.processEscrows = MagicMock()
        client = testing.TestClient(build_app())
        result = client.simulate_post(
            "/", body=fwd_serder.raw,
            headers={"Content-Type": "application/cesr"}
        )

    assert result.status_code == 204, f"expected 204, got {result.status_code}"
    assert len(sent_calls) == 1, f"expected 1 nudge, got {sent_calls}"
    assert sent_calls[0]["ConnectionId"] == "conn-abc-123"
    nudge = json.loads(sent_calls[0]["Data"])
    assert nudge["type"] == "mailbox.nudge"
    assert nudge["pre"] == "Ebob"
    assert nudge["topic"] == "credential"


@needs_moto
def test_fwd_deposit_gone_exception_reaps_stale_row_and_returns_204(nudge_conn_table, monkeypatch):
    """GoneException from post_to_connection → registry row deleted + 204."""
    from mailbox_handler import build_app, _notify_subscribers

    nudge_conn_table.put_item(Item={
        "connectionId": "conn-stale-gone",
        "pre": "Ebob",
        "topics": {"credential": 0},
        "connectedAt": 0,
        "expireAt": 9999999999,
    })

    fwd_serder = _make_fwd_serder(pre="Ebob", topic="credential")

    # Simulate GoneException from the API GW management client.
    fake_apigw = MagicMock()
    fake_apigw.exceptions.GoneException = Exception
    fake_apigw.post_to_connection.side_effect = fake_apigw.exceptions.GoneException("gone")

    with patch("mailbox_handler._hby") as mock_hby, \
         patch("mailbox_handler._detect_mbx_query", return_value=None), \
         patch("boto3.client", return_value=fake_apigw):
        mock_hby.psr.parse = MagicMock()
        mock_hby.kvy.processEscrows = MagicMock()
        client = testing.TestClient(build_app())
        result = client.simulate_post(
            "/", body=fwd_serder.raw,
            headers={"Content-Type": "application/cesr"}
        )

    assert result.status_code == 204, f"expected 204, got {result.status_code}"

    # The stale registry row must have been deleted.
    remaining = nudge_conn_table.get_item(Key={"connectionId": "conn-stale-gone"})
    assert "Item" not in remaining, "stale row should have been deleted on GoneException"


@needs_moto
def test_fwd_deposit_arbitrary_notify_error_still_returns_204(nudge_conn_table, monkeypatch):
    """An arbitrary boto error in _notify_subscribers NEVER fails the 204 deposit."""
    from mailbox_handler import build_app

    nudge_conn_table.put_item(Item={
        "connectionId": "conn-error",
        "pre": "Ebob",
        "topics": {"credential": 0},
        "connectedAt": 0,
        "expireAt": 9999999999,
    })

    fwd_serder = _make_fwd_serder(pre="Ebob", topic="credential")

    # post_to_connection raises a generic (non-Gone) error.
    fake_apigw = MagicMock()
    fake_apigw.exceptions.GoneException = type("GoneException", (Exception,), {})
    fake_apigw.post_to_connection.side_effect = RuntimeError("arbitrary boto failure")

    with patch("mailbox_handler._hby") as mock_hby, \
         patch("mailbox_handler._detect_mbx_query", return_value=None), \
         patch("boto3.client", return_value=fake_apigw):
        mock_hby.psr.parse = MagicMock()
        mock_hby.kvy.processEscrows = MagicMock()
        client = testing.TestClient(build_app())
        result = client.simulate_post(
            "/", body=fwd_serder.raw,
            headers={"Content-Type": "application/cesr"}
        )

    # The deposit MUST return 204 regardless of the nudge failure.
    assert result.status_code == 204, (
        f"nudge error must not block the 204 deposit; got {result.status_code}"
    )
