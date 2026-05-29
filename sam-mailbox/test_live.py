"""End-to-end live mailbox conformance tests.

Exercises the deployed Lambda mailbox as a third-party KERI controller would.
Default target is ``https://mailbox.keri.host``; override via ``MAILBOX_URL``
environment variable (e.g. ``http://localhost:3000`` for ``sam local``).

Each test uses a fresh in-memory ``Habery`` so tests are order-independent
and can run in CI.

Run with::

    pytest sam-mailbox/test_live.py -v
    MAILBOX_URL=http://localhost:3000 pytest sam-mailbox/test_live.py -v
"""

import json
import os
import socket
import tempfile
import threading
import time
import urllib.request
import urllib.error

import pytest

os.environ.setdefault("HOME", tempfile.mkdtemp(prefix="mailbox-live-test-"))

from keri.app.habbing import Habery        # noqa: E402
from keri.core.signing import Salter       # noqa: E402


MAILBOX_URL = os.environ.get("MAILBOX_URL", "https://mailbox.keri.host").rstrip("/")
TIMEOUT = 30  # seconds per HTTP call


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mailbox_pre():
    """Discover the mailbox's own AID via the JSON status endpoint."""
    with urllib.request.urlopen(f"{MAILBOX_URL}/", timeout=TIMEOUT) as r:
        body = json.loads(r.read())
    pre = body["mailbox"]
    assert pre.startswith("B"), f"mailbox AID is not non-trans: {pre!r}"
    return pre


@pytest.fixture
def fresh_hby():
    """Spin up a fresh in-memory Habery for one test."""
    hby = Habery(name="t", temp=True, salt=Salter().qb64)
    yield hby
    hby.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class CaseInsensitiveHeaders(dict):
    """Wrap response headers so lookups are case-insensitive (the server may
    emit `keri-aid` while we test for `KERI-AID`)."""
    def __init__(self, headers):
        super().__init__({k.lower(): v for k, v in headers.items()})

    def __getitem__(self, key):
        return super().__getitem__(key.lower())

    def get(self, key, default=None):
        return super().get(key.lower(), default)

    def __contains__(self, key):
        return super().__contains__(key.lower())


def http_get(path, accept="application/cesr"):
    req = urllib.request.Request(f"{MAILBOX_URL}{path}",
                                 headers={"Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, CaseInsensitiveHeaders(r.headers), r.read()


def http_post_cesr(path, body, read_response=True):
    req = urllib.request.Request(
        f"{MAILBOX_URL}{path}",
        data=bytes(body),
        headers={"Content-Type": "application/cesr",
                 "Accept": "application/cesr"},
        method="POST",
    )
    r = urllib.request.urlopen(req, timeout=TIMEOUT)
    if read_response:
        try:
            return r.status, CaseInsensitiveHeaders(r.headers), r.read()
        finally:
            r.close()
    return r  # caller owns lifecycle (used by streaming reader)


def _make_fwd_message(sender_hab, recipient_pre, topic, embedded_msg):
    """Build a signed /fwd exn message ready to POST to the mailbox.

    Mirrors keripy's reference fwd construction at
    keri.app.forwarding.StreamPoster._fwd:
      - exchange(route="/fwd", sender=alice.pre, modifiers={pre, topic},
                 embeds={"msg": inner_bytes})
      - hab.endorse(serder) signs the exn with the sender's keys.
    """
    from keri.peer.exchanging import exchange
    fwd_serder, fwd_end = exchange(
        route="/fwd",
        sender=sender_hab.pre,
        modifiers={"pre": recipient_pre, "topic": topic},
        payload={},
        embeds={"msg": bytes(embedded_msg)},
    )
    signed = sender_hab.endorse(fwd_serder, last=False)
    signed.extend(fwd_end)
    return bytes(signed)


def _make_mbx_query(querier_hab, recipient_pre, topics, target_pre):
    """Build a signed `qry r=/mbx` message ready to POST to the mailbox.

    Mirrors keri.app.indirecting.Poller construction:
        q = dict(pre=self.pre, topics=topics)
        hab.query(pre=self.pre, src=self.mailbox, route="mbx", query=q)
    """
    q = dict(pre=recipient_pre, topics=topics)
    return bytes(querier_hab.query(
        pre=recipient_pre,
        src=target_pre,
        route="mbx",
        query=q,
    ))


def _read_sse_until(qry_ims, expected_substr, max_seconds=12):
    """POST `qry_ims` and read the streaming SSE body until `expected_substr`
    appears in the accumulated body, OR `max_seconds` elapse.

    Returns (status, headers, body_text). Streaming reads use short socket
    timeouts so we don't get stuck on a long-poll keepalive window.
    """
    req = urllib.request.Request(
        f"{MAILBOX_URL}/", data=bytes(qry_ims),
        headers={"Content-Type": "application/cesr"}, method="POST",
    )
    r = urllib.request.urlopen(req, timeout=max_seconds)
    status = r.status
    headers = CaseInsensitiveHeaders(r.headers)
    try:
        # Per-read socket timeout (separate from the open timeout)
        r.fp.raw._sock.settimeout(2.0)
    except Exception:
        pass
    buf = bytearray()
    deadline = time.monotonic() + max_seconds
    try:
        while time.monotonic() < deadline:
            try:
                chunk = r.read(512)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf.extend(chunk)
            if expected_substr.encode("utf-8") in buf:
                break
    finally:
        try:
            r.close()
        except Exception:
            pass
    return status, headers, buf.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_status_endpoint_returns_mailbox_metadata():
    """GET / returns JSON with mailbox AID and identifier metadata."""
    status, headers, body = http_get("/", accept="application/json")
    assert status == 200
    data = json.loads(body)
    assert data["mailbox"].startswith("B"), data
    assert data["alias"] == "mailbox"
    assert data["sn"] == 0       # non-trans inception only


def test_oobi_returns_signed_cesr_stream(mailbox_pre):
    """GET /oobi returns CESR with KERI-AID header containing KEL + rpys."""
    status, headers, body = http_get("/oobi", accept="application/cesr")
    assert status == 200
    assert headers["Content-Type"] == "application/cesr"
    assert headers["KERI-AID"] == mailbox_pre
    assert b'"v":"KERI10' in body[:200], f"body not raw CESR: {body[:80]!r}"
    assert b'"t":"icp"' in body
    assert b'"r":"/loc/scheme"' in body
    assert b'"r":"/end/role/add"' in body


def test_oobi_advertises_mailbox_role(mailbox_pre):
    """The mailbox's OOBI must include /end/role/add for role=mailbox."""
    _, _, body = http_get(f"/oobi/{mailbox_pre}/mailbox")
    text = body.decode("utf-8")
    assert '"role":"mailbox"' in text, \
        f"mailbox role not in OOBI: {text[:400]!r}"
    assert mailbox_pre in text


def test_oobi_does_not_advertise_witness_role(mailbox_pre):
    """The mailbox must NOT advertise role=witness for itself (role split)."""
    _, _, body = http_get(f"/oobi/{mailbox_pre}/mailbox")
    assert b'"role":"witness"' not in body, \
        "mailbox accidentally advertises witness role"


def test_post_empty_body_returns_400():
    """POST / with empty body returns 400 (basic input validation)."""
    req = urllib.request.Request(
        f"{MAILBOX_URL}/", data=b"",
        headers={"Content-Type": "application/cesr"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT)
        pytest.fail("expected HTTPError 400, got 2xx response")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400, f"expected 400, got {exc.code}"


def test_kel_post_returns_204(fresh_hby):
    """Submitting a controller's signed icp event returns 204."""
    alice = fresh_hby.makeHab(name="alice", transferable=False,
                              isith="1", icount=1, ncount=0, nsith="0")
    status, _, _ = http_post_cesr("/", alice.msgOwnEvent(sn=0))
    assert status == 204


def test_mbx_query_returns_streaming_response(fresh_hby, mailbox_pre):
    """POST signed qry r=/mbx returns 200 + Content-Type: text/event-stream.

    Just opens the stream and confirms the header — doesn't try to read
    the body (which would hold the connection open for up to 5 min).
    """
    bob = fresh_hby.makeHab(name="bob", transferable=False,
                            isith="1", icount=1, ncount=0, nsith="0")
    # Mailbox needs Bob's KEL to verify the qry signature
    s1, _, _ = http_post_cesr("/", bob.msgOwnEvent(sn=0))
    assert s1 == 204

    qry_ims = _make_mbx_query(
        querier_hab=bob,
        recipient_pre=bob.pre,
        topics={"/credential": -1, "/receipt": -1},
        target_pre=mailbox_pre,
    )
    req = urllib.request.Request(
        f"{MAILBOX_URL}/", data=qry_ims,
        headers={"Content-Type": "application/cesr"}, method="POST",
    )
    r = urllib.request.urlopen(req, timeout=15)
    try:
        assert r.status == 200
        assert r.headers.get("Content-Type") == "text/event-stream"
    finally:
        r.close()


@pytest.mark.xfail(
    reason=(
        "keripy psr.parse(framed=True) hangs on the /fwd exn structure "
        "produced by keri.peer.exchanging.exchange(embeds={'msg': ...}). "
        "Reproduces against a fresh in-memory Habery (no Lambda/Falcon "
        "involved), so this is a keripy parser issue with the embed/path "
        "counter framing rather than a sam-mailbox bug. The mailbox handler "
        "wires ForwardHandler correctly; if/when the parse hang is resolved "
        "this test should start passing without code changes here."
    ),
    strict=False,
    run=True,
)
def test_deposit_then_poll_round_trip(fresh_hby, mailbox_pre):
    """Alice POSTs /fwd to mailbox; Bob POSTs qry r=/mbx and the deposited
    message arrives on the SSE stream within the initial drain window.

    End-to-end verification of:
      - ForwardHandler registration routes /fwd to mailbox storage
      - Signed-qry signature verification against a known KEL
      - Streaming generator emits the queued message in the first poll
        cycle (no need to wait for live arrivals or keepalives)
    """
    # Alice (sender) and Bob (recipient) — trans AIDs so /fwd can verify
    alice = fresh_hby.makeHab(name="alice", transferable=True,
                              isith="1", icount=1, ncount=1, nsith="1")
    bob = fresh_hby.makeHab(name="bob", transferable=True,
                            isith="1", icount=1, ncount=1, nsith="1")

    # Mailbox needs Alice's KEL to verify her /fwd exn signature
    s_a, _, _ = http_post_cesr("/", alice.msgOwnEvent(sn=0))
    assert s_a == 204, f"alice icp publish: {s_a}"
    # And Bob's KEL to verify his qry signature
    s_b, _, _ = http_post_cesr("/", bob.msgOwnEvent(sn=0))
    assert s_b == 204, f"bob icp publish: {s_b}"

    # Alice forwards a small inner message to Bob's /credential topic
    embedded = bob.msgOwnEvent(sn=0)  # any opaque CESR will do
    fwd_ims = _make_fwd_message(
        sender_hab=alice,
        recipient_pre=bob.pre,
        topic="/credential",
        embedded_msg=embedded,
    )
    s_fwd, _, _ = http_post_cesr("/", fwd_ims)
    assert s_fwd == 204, f"fwd POST: {s_fwd}"

    # Now Bob polls — initial drain should yield the just-deposited message
    qry_ims = _make_mbx_query(
        querier_hab=bob,
        recipient_pre=bob.pre,
        topics={"/credential": -1},
        target_pre=mailbox_pre,
    )
    status, headers, body_text = _read_sse_until(
        qry_ims, expected_substr="event: /credential", max_seconds=12,
    )
    assert status == 200, f"poll status: {status}"
    assert headers.get("Content-Type") == "text/event-stream", \
        f"poll content type: {headers.get('Content-Type')!r}"
    assert "event: /credential" in body_text, \
        f"missing event:/credential in SSE body: {body_text[:400]!r}"
    assert "id: 0" in body_text, \
        f"missing id:0 in SSE body: {body_text[:400]!r}"


def test_mbx_query_missing_q_pre_returns_400(fresh_hby, mailbox_pre):
    """Malformed mbx qry (no q.pre) returns 400, not 500/stream."""
    bob = fresh_hby.makeHab(name="bob", transferable=False,
                            isith="1", icount=1, ncount=0, nsith="0")
    s_b, _, _ = http_post_cesr("/", bob.msgOwnEvent(sn=0))
    assert s_b == 204

    # Build a qry with no `pre` in the query body
    q = dict(topics={"/credential": -1})  # NO 'pre'
    qry_ims = bytes(bob.query(
        pre=bob.pre, src=mailbox_pre, route="mbx", query=q,
    ))
    req = urllib.request.Request(
        f"{MAILBOX_URL}/", data=qry_ims,
        headers={"Content-Type": "application/cesr"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT)
        pytest.fail("expected HTTPError 400, got 2xx response")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400, f"expected 400, got {exc.code}"
