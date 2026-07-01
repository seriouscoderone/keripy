"""End-to-end live mailbox conformance tests.

Exercises the deployed Lambda mailbox as a third-party KERI controller would.
Default target is ``https://mailbox.keri.host``; override via ``MAILBOX_URL``
environment variable (e.g. ``https://dev.mailbox.keri.host`` for a dev stage).

Each test uses a fresh in-memory ``Habery`` so tests are order-independent
and can run in CI.

These tests target the **serverless notify-and-fetch** flow introduced in
Phase 3 (§5.3 / §5.5 / §5.7):

  1. Subscribe — open a WebSocket and send a signed ``action=subscribe`` envelope.
  2. Deposit  — POST ``/fwd`` over REST to deliver a message.
  3. Nudge    — assert a ``mailbox.nudge`` frame arrives on the WS.
  4. Drain    — POST a signed ``qry r=/mbx``; assert the response delivers the
                backlog and **closes** (drain-then-EOF), NOT a long-poll stream.

Run with::

    MAILBOX_URL=https://dev.mailbox.keri.host pytest keri_cdk/probes/mailbox_conformance/probe.py -v
    # point at a local SAM / moto dev stack:
    MAILBOX_URL=http://localhost:3000 pytest keri_cdk/probes/mailbox_conformance/probe.py -v

The default target ``https://mailbox.keri.host`` is the **production** mailbox.
Do NOT run the deposit/nudge/drain tests against production without explicit
approval; only run the status/oobi/400-path tests against it.
Task 8 runs the full suite against a dev stage.
"""

import base64
import json
import os
import tempfile
import time
import urllib.request
import urllib.error

import pytest
import websockets.sync.client

os.environ.setdefault("HOME", tempfile.mkdtemp(prefix="mailbox-live-test-"))

from keri.app.habbing import Habery        # noqa: E402
from keri.core.signing import Salter       # noqa: E402


MAILBOX_URL = os.environ.get("MAILBOX_URL", "https://mailbox.keri.host").rstrip("/")
TIMEOUT = 30  # seconds per HTTP call
NUDGE_TIMEOUT = 15  # seconds to wait for a WS nudge frame


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


@pytest.fixture(scope="module")
def mailbox_ws_url():
    """Discover the ``wss://`` URL from the status endpoint ``ws`` field."""
    with urllib.request.urlopen(f"{MAILBOX_URL}/", timeout=TIMEOUT) as r:
        body = json.loads(r.read())
    ws = body.get("ws", "")
    assert ws.startswith("wss://") or ws.startswith("ws://"), \
        f"status JSON missing valid ws field: {body!r}"
    return ws


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
    return r  # caller owns lifecycle


def _make_fwd_message(sender_hab, recipient_pre, topic, embedded_msg):
    """Build a signed /fwd exn message ready to POST to the mailbox.

    Mirrors keripy's reference fwd construction at
    keri.app.forwarding.StreamPoster._fwd:
      - exchange(route="/fwd", sender=alice.pre, modifiers={pre, topic},
                 embeds={"msg": inner_bytes})
      - hab.endorse(serder) signs the exn with the sender's keys.
    """
    from keri.core.eventing import exchange
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
    """Build a signed ``qry r=/mbx`` message ready to POST to the mailbox.

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


def _drain_mbx_response(qry_ims, max_seconds=12):
    """POST ``qry_ims`` and read the response to completion (drain-then-EOF).

    For the serverless one-shot fetch, the mailbox returns the backlog and
    then **closes** the connection — no long-poll keepalive.  This helper
    reads until EOF within ``max_seconds``, then returns the accumulated body.

    Returns (status, headers, body_bytes).
    """
    req = urllib.request.Request(
        f"{MAILBOX_URL}/", data=bytes(qry_ims),
        headers={"Content-Type": "application/cesr"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=max_seconds) as r:
        status = r.status
        headers = CaseInsensitiveHeaders(r.headers)
        body = r.read()
    return status, headers, body


def _ws_subscribe(ws_url, querier_hab, recipient_pre, topics, target_pre):
    """Open a sync WS to ``ws_url``, send an ``action=subscribe`` envelope,
    and return the live ``websockets.sync.client.Connection`` object.

    The envelope carries a base64-encoded signed ``qry r=/mbx`` so the
    server can verify the subscriber owns (or queries on behalf of) ``recipient_pre``.

    Caller is responsible for closing the connection.
    """
    qry_bytes = _make_mbx_query(querier_hab, recipient_pre, topics, target_pre)
    qry_b64 = base64.b64encode(qry_bytes).decode("ascii")
    envelope = json.dumps({"action": "subscribe", "qry": qry_b64})
    conn = websockets.sync.client.connect(ws_url)
    conn.send(envelope)
    return conn


# ---------------------------------------------------------------------------
# tests — status / OOBI / error paths (safe to run against any target)
# ---------------------------------------------------------------------------

def test_status_endpoint_returns_mailbox_metadata():
    """GET / returns JSON with mailbox AID, alias, sn, mode, and ws fields."""
    status, headers, body = http_get("/", accept="application/json")
    assert status == 200
    data = json.loads(body)
    assert data["mailbox"].startswith("B"), data
    # Aliases vary per pool member (e.g. "mailbox", "mailbox-legitim").
    # All start with "mailbox".
    assert data["alias"].startswith("mailbox")
    assert data["sn"] == 0       # non-trans inception only
    assert data.get("mode") == "notify-and-fetch", \
        f"expected mode=notify-and-fetch in status: {data!r}"
    assert data.get("ws"), "status JSON missing ws field"


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


def test_mbx_query_returns_onboarding_headers(fresh_hby, mailbox_pre):
    """POST signed qry r=/mbx returns 200 with serverless onboarding headers.

    The serverless one-shot flow sets:
      X-Mailbox-Mode: notify-and-fetch
      X-Mailbox-Client: (optional hint for library version negotiation)

    The response body is the drained backlog (may be empty); it CLOSES after
    the drain completes — NOT a long-lived SSE stream.
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
    status, headers, body = _drain_mbx_response(qry_ims, max_seconds=12)
    assert status == 200, f"mbx qry status: {status}"
    assert headers.get("X-Mailbox-Mode") == "notify-and-fetch", \
        f"missing/wrong X-Mailbox-Mode: {dict(headers)!r}"
    # X-Mailbox-Client is optional — assert it is present (may be empty string)
    assert "x-mailbox-client" in headers, \
        f"X-Mailbox-Client header missing: {dict(headers)!r}"
    # Response must close (body is a complete, finite drain — not streaming)
    # The assertion is structural: _drain_mbx_response reads until EOF, which
    # would time-out on a 780-second long-poll.  Reaching here proves closure.


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


# ---------------------------------------------------------------------------
# tests — serverless notify-and-fetch flow (require dev stage, Task 8)
# ---------------------------------------------------------------------------

def test_ws_subscribe_then_deposit_nudge_drain(fresh_hby, mailbox_pre, mailbox_ws_url):
    """Full serverless notify-and-fetch round-trip (§5.3 / §5.5 / §5.7).

    Steps (in sequence):

      (a) WS subscribe — open a WebSocket to the wss:// URL discovered from
          the status JSON ``ws`` field; send a signed subscribe envelope.
      (b) Deposit     — POST a signed ``/fwd`` exn depositing a message for
          Bob's ``/credential`` topic.
      (c) Nudge       — assert a ``mailbox.nudge`` JSON frame arrives on the
          WS within NUDGE_TIMEOUT seconds.
      (d) Drain       — POST a signed ``qry r=/mbx``; assert the response
          delivers the deposited message AND closes (drain-then-EOF, NOT
          a long-poll stream).  Assert ``X-Mailbox-Mode: notify-and-fetch``.
    """
    alice = fresh_hby.makeHab(name="alice", transferable=True,
                              isith="1", icount=1, ncount=1, nsith="1")
    bob = fresh_hby.makeHab(name="bob", transferable=True,
                            isith="1", icount=1, ncount=1, nsith="1")

    # Mailbox needs both KELs to verify signatures
    s_a, _, _ = http_post_cesr("/", alice.msgOwnEvent(sn=0))
    assert s_a == 204, f"alice icp publish: {s_a}"
    s_b, _, _ = http_post_cesr("/", bob.msgOwnEvent(sn=0))
    assert s_b == 204, f"bob icp publish: {s_b}"

    # (a) WS subscribe — Bob subscribes for his own /credential topic
    conn = _ws_subscribe(
        ws_url=mailbox_ws_url,
        querier_hab=bob,
        recipient_pre=bob.pre,
        topics={"/credential": -1},
        target_pre=mailbox_pre,
    )
    try:
        # (b) Deposit — Alice forwards a small inner message to Bob's /credential topic
        embedded = bob.msgOwnEvent(sn=0)  # any opaque CESR bytes will do
        fwd_ims = _make_fwd_message(
            sender_hab=alice,
            recipient_pre=bob.pre,
            topic="/credential",
            embedded_msg=embedded,
        )
        s_fwd, _, _ = http_post_cesr("/", fwd_ims)
        assert s_fwd == 204, f"fwd POST: {s_fwd}"

        # (c) Nudge — wait for mailbox.nudge frame on the WS
        deadline = time.monotonic() + NUDGE_TIMEOUT
        nudge_frame = None
        while time.monotonic() < deadline:
            try:
                remaining = max(0.5, deadline - time.monotonic())
                raw = conn.recv(timeout=remaining)
                frame = json.loads(raw)
                if frame.get("type") == "mailbox.nudge":
                    nudge_frame = frame
                    break
            except TimeoutError:
                break
        assert nudge_frame is not None, \
            "no mailbox.nudge frame received within timeout"
        assert nudge_frame.get("pre") == bob.pre, \
            f"nudge.pre mismatch: {nudge_frame!r}"
        assert nudge_frame.get("topic") == "/credential", \
            f"nudge.topic mismatch: {nudge_frame!r}"
    finally:
        conn.close()

    # (d) Drain — one-shot fetch: response delivers backlog and CLOSES
    qry_ims = _make_mbx_query(
        querier_hab=bob,
        recipient_pre=bob.pre,
        topics={"/credential": -1},
        target_pre=mailbox_pre,
    )
    status, headers, body = _drain_mbx_response(qry_ims, max_seconds=12)
    assert status == 200, f"drain status: {status}"
    assert headers.get("X-Mailbox-Mode") == "notify-and-fetch", \
        f"missing X-Mailbox-Mode: {dict(headers)!r}"
    # The deposited message must appear in the drained body (as an SSE event
    # or raw CESR; the probe checks presence of the /credential topic marker)
    body_text = body.decode("utf-8", errors="replace")
    assert "/credential" in body_text, \
        f"deposited message not in drained body: {body_text[:400]!r}"
    # Reaching here proves the response closed; a 780-second long-poll would
    # have timed out _drain_mbx_response before we reached this assertion.
