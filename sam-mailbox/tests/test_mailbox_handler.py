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

    Falcon TestClient drains async generator streams into result.text, so the
    assertion works identically whether resp.stream or resp.text was used.
    """
    import asyncio as _asyncio_inner
    from mailbox_handler import build_app
    fake_serder = MagicMock()
    fake_serder.ked = {
        "t": "qry", "r": "/mbx",
        "q": {"pre": "BRecipient", "topics": {"receipt": 0}}
    }
    sse_chunk = b"id: 0\nevent: receipt\nretry: 1000\ndata: msg\n\n"

    async def fake_stream(pre, topics, **kwargs):
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
# Task 2.7: _stream_mbx_response async generator (SSE long-poll)
# ---------------------------------------------------------------------------

import asyncio as _asyncio_for_tests


@pytest.mark.asyncio
async def test_stream_mbx_response_yields_initial_drain():
    """Initial drain yields one SSE frame per queued message."""
    from mailbox_handler import _stream_mbx_response
    with patch("mailbox_handler._hby") as mock_hby:
        # First poll returns 2 messages, subsequent polls return empty
        mock_hby.db.cloneTopicIter = MagicMock(side_effect=[
            iter([(0, b"BRecipient/receipt", b"msg-one"),
                  (1, b"BRecipient/receipt", b"msg-two")]),
            iter([]),  # second poll
            iter([]),  # third poll
            iter([]),  # fourth poll
        ])
        # Short soft_cap to bound the loop
        gen = _stream_mbx_response("BRecipient", {"receipt": 0},
                                   soft_cap_s=0.3, poll_interval_s=0.05,
                                   keepalive_interval_s=60)
        frames = []
        async for frame in gen:
            frames.append(frame)
    body = b"".join(frames)
    assert b"data: msg-one" in body
    assert b"data: msg-two" in body
    assert b"id: 0" in body
    assert b"id: 1" in body


@pytest.mark.asyncio
async def test_stream_mbx_response_emits_keepalive_when_idle():
    """When no new messages, emits :keepalive after keepalive_interval_s."""
    from mailbox_handler import _stream_mbx_response
    with patch("mailbox_handler._hby") as mock_hby:
        mock_hby.db.cloneTopicIter = MagicMock(return_value=iter([]))
        gen = _stream_mbx_response("BRecipient", {"receipt": 0},
                                   soft_cap_s=0.25, poll_interval_s=0.02,
                                   keepalive_interval_s=0.1)
        frames = []
        async for frame in gen:
            frames.append(frame)
    assert any(f == b":keepalive\n\n" for f in frames), \
        f"expected a :keepalive frame, got: {frames}"


@pytest.mark.asyncio
async def test_stream_mbx_response_advances_cursor_across_polls():
    """If new messages arrive on a later poll, they get yielded with correct ids."""
    from mailbox_handler import _stream_mbx_response

    # Simulate: first poll drains nothing, second poll yields one new message
    call_count = {"n": 0}
    def fake_iter(topic, fn):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return iter([])
        elif call_count["n"] == 2:
            return iter([(5, topic, b"late-arrival")])
        else:
            return iter([])

    with patch("mailbox_handler._hby") as mock_hby:
        mock_hby.db.cloneTopicIter = fake_iter
        gen = _stream_mbx_response("BRecipient", {"receipt": 0},
                                   soft_cap_s=0.3, poll_interval_s=0.05,
                                   keepalive_interval_s=60)
        frames = []
        async for frame in gen:
            frames.append(frame)
    body = b"".join(frames)
    assert b"data: late-arrival" in body
    assert b"id: 5" in body


# ---------------------------------------------------------------------------
# Task 2.8: init() cold-start validation
# ---------------------------------------------------------------------------

def test_init_requires_mailbox_salt(monkeypatch):
    """init() must raise if MAILBOX_SALT is missing — never mint a non-recoverable AID."""
    import mailbox_handler
    import importlib

    # Re-import to get the original (un-mocked) init function.
    # The autouse fixture mocked mailbox_handler.init, but we can reload
    # the module in-process and grab the real function from a fresh import.
    fresh = importlib.import_module("mailbox_handler")
    # importlib.reload returns the same module object in-process; the autouse
    # fixture has already replaced fresh.init. Instead, import the real function
    # from source by directly referencing the module attribute before the mock
    # runs — but since autouse has already run, we instead call the function
    # object from the module's __dict__ under its original name by undoing the
    # monkeypatch for 'mailbox_handler.init' and then setting _initialized=False.
    monkeypatch.undo()  # undo ALL monkeypatches so far, including autouse mock
    monkeypatch.delenv("MAILBOX_SALT", raising=False)
    monkeypatch.delenv("MAILBOX_SALT_SECRET", raising=False)
    monkeypatch.setattr(mailbox_handler, "_initialized", False)
    with pytest.raises(RuntimeError) as exc_info:
        mailbox_handler.init()
    assert "MAILBOX_SALT" in str(exc_info.value)


def test_load_salt_direct_override_is_local_dev_path():
    """A direct qb64 salt (local/dev MAILBOX_SALT) is returned as-is, no AWS call."""
    import mailbox_handler
    assert mailbox_handler._load_salt(None, "us-east-1", direct="0AB123") == "0AB123"


def test_load_salt_returns_none_when_nothing_configured():
    """No secret + no direct override ⇒ None (caller raises; never mints)."""
    import mailbox_handler
    assert mailbox_handler._load_salt(None, "us-east-1") is None
    # The Secrets Manager fetch branch (secret_id set) uses the same boto3
    # pattern proven by service-aid's test_load_bran_from_secrets_manager (moto).
