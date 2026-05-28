"""Unit tests for mailbox_handler — no AWS, no DynamoDB."""

import json
from unittest.mock import patch, MagicMock

import falcon
from falcon import testing

from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import eventing


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
