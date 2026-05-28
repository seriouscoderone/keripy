"""End-to-end live witness conformance tests.

Exercises the deployed Lambda witness as a third-party KERI controller would.
Default target is ``https://witness.keri.host``; override via ``WITNESS_URL``
environment variable (e.g. ``http://localhost:3000`` for ``sam local``).

Each test uses a fresh in-memory ``Habery`` (no persisted state) so tests are
order-independent and can run in CI.

Run with::

    pytest sam-witness/test_live.py -v
    WITNESS_URL=http://localhost:3000 pytest sam-witness/test_live.py -v
"""

import base64
import json
import os
import tempfile
import urllib.request
import urllib.error

import pytest

# Use a dedicated HOME so the Habery's filesystem-backed bits don't collide
# with the user's ~/.keri.  Set BEFORE keripy imports.
os.environ.setdefault("HOME", tempfile.mkdtemp(prefix="witness-live-test-"))

from keri.app.habbing import Habery        # noqa: E402
from keri.core.coring import Verfer        # noqa: E402
from keri.core.signing import Salter       # noqa: E402


WITNESS_URL = os.environ.get("WITNESS_URL", "https://witness.keri.host").rstrip("/")
TIMEOUT = 30  # seconds per HTTP call


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def witness_pre():
    """Discover the witness's own AID via the JSON status endpoint."""
    with urllib.request.urlopen(f"{WITNESS_URL}/", timeout=TIMEOUT) as r:
        body = json.loads(r.read())
    pre = body["witness"]
    assert pre.startswith("B"), f"witness AID is not non-trans: {pre!r}"
    return pre


@pytest.fixture
def fresh_hby():
    """Spin up a fresh in-memory Habery for one test."""
    hby = Habery(name="t", temp=True, salt=Salter().qb64)
    yield hby
    hby.close()


def http_get(path, accept="application/cesr"):
    req = urllib.request.Request(f"{WITNESS_URL}{path}",
                                 headers={"Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, dict(r.headers), r.read()


def http_post_cesr(path, body):
    req = urllib.request.Request(
        f"{WITNESS_URL}{path}",
        data=bytes(body),
        headers={"Content-Type": "application/cesr",
                 "Accept": "application/cesr"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, dict(r.headers), r.read()


def split_event_and_attachment(cesr):
    """Split a CESR stream of one event into (event_serder_bytes, attachment_bytes).

    Mirrors the format ``streamCESRRequests`` produces (body holds the
    serder, attachments go in the ``CESR-ATTACHMENT`` header).
    """
    cesr = bytes(cesr)
    nesting, end = 0, 0
    for i, b in enumerate(cesr):
        if b == 0x7B:    # {
            nesting += 1
        elif b == 0x7D:  # }
            nesting -= 1
            if nesting == 0:
                end = i + 1
                break
    return cesr[:end], cesr[end:]


def _make_fwd_message(sender_hab, recipient_pre, topic, embedded_msg):
    """Build a signed /fwd exn message ready to POST to the witness.

    Mirrors keripy's reference fwd construction (see
    tests/core/test_parsing_pathed.py:39-43 and
    keri.app.forwarding.StreamPoster._fwd at line 192).

    Args:
        sender_hab: the Hab signing the /fwd
        recipient_pre (str): qb64 AID of the eventual recipient
        topic (str): topic name to deposit under (e.g. "/credential")
        embedded_msg (bytes): the inner KERI message to forward (full
            CESR ims, including any attachments)

    Returns:
        bytes: signed /fwd exn ims (body + attachments) ready to POST
    """
    from keri.peer.exchanging import exchange
    fwd_serder, fwd_end = exchange(
        route="/fwd",
        sender=sender_hab.pre,
        modifiers={"pre": recipient_pre, "topic": topic},
        payload={},
        embeds={"msg": bytes(embedded_msg)},
    )
    signed = sender_hab.endorse(fwd_serder, last=False, pipelined=False)
    signed.extend(fwd_end)
    return bytes(signed)


def _make_mbx_query(querier_hab, recipient_pre, topics, witness_pre):
    """Build a signed `qry r=mbx` message ready to POST to the witness.

    Mirrors the reference Poller construction at
    keri/app/indirecting.py:809-811:

        q = dict(pre=self.pre, topics=self.topics.topics)
        msg = hab.query(pre=self.pre, src=self.witness,
                        route="mbx", query=q)

    Args:
        querier_hab: the Hab signing the query (typically the recipient
            asking for their own mailbox)
        recipient_pre (str): the AID whose mailbox to read (typically
            the same as querier_hab.pre)
        topics (dict): {topic_name: last_seen_ordinal}
        witness_pre (str): qb64 of the attester being queried (the
            witness AID)

    Returns:
        bytes: signed qry ims ready to POST
    """
    q = dict(pre=recipient_pre, topics=topics)
    return bytes(querier_hab.query(
        pre=recipient_pre,
        src=witness_pre,
        route="mbx",
        query=q,
    ))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_status_endpoint_returns_witness_metadata():
    """GET / returns JSON with witness AID and identifier metadata."""
    status, headers, body = http_get("/", accept="application/json")
    assert status == 200
    data = json.loads(body)
    assert data["witness"].startswith("B"), data
    assert data["alias"] == "witness"
    assert data["sn"] == 0       # non-trans inception only


def test_oobi_returns_signed_cesr_stream(witness_pre):
    """GET /oobi returns CESR with KERI-AID header; body contains KEL + replies."""
    status, headers, body = http_get("/oobi", accept="application/cesr")
    assert status == 200
    assert headers["Content-Type"] == "application/cesr"
    assert headers["KERI-AID"] == witness_pre

    # Body should be raw CESR (not base64-text — API Gateway should decode
    # because Accept matches BinaryMediaTypes).
    assert b'"v":"KERI10' in body[:200], (
        f"body not raw CESR (API Gateway BinaryMediaTypes misconfigured?): "
        f"{body[:80]!r}"
    )

    # Stream should contain one inception, one /loc/scheme reply, one
    # /end/role/add reply.
    assert b'"t":"icp"' in body
    assert b'"r":"/loc/scheme"' in body
    assert b'"r":"/end/role/add"' in body


def test_oobi_round_trip_a_fresh_habery_can_resolve(fresh_hby, witness_pre):
    """A KERI agent that knows nothing about us can bootstrap trust via OOBI.

    Uses the bare ``/oobi`` URL (default role). This is the witness's own
    self-OOBI: returns its KEL plus the signed ``/loc/scheme`` and
    ``/end/role/add`` replies that bind its AID to its URL.

    Note: ``/oobi/{aid}/witness`` returns only the KEL because our witness
    does not list itself in its own ``wits`` (non-trans witnesses don't have
    witnesses). The bare or ``controller``-role form is the right discovery
    URL for a self-OOBI.
    """
    _, _, oobi = http_get("/oobi")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    assert witness_pre in fresh_hby.kevers, (
        "witness KEL not registered after OOBI parse — signatures may not verify"
    )
    kever = fresh_hby.kevers[witness_pre]
    assert kever.sn == 0
    assert kever.verfers[0].qb64 == witness_pre  # non-trans: AID == verfer

    # The signed /loc/scheme reply should bind witness_pre to its URL.
    scheme = "https" if WITNESS_URL.startswith("https") else "http"
    loc = fresh_hby.db.locs.get(keys=(witness_pre, scheme))
    assert loc is not None, f"no /loc/scheme reply persisted in db.locs[{witness_pre},{scheme}]"
    assert loc.url == WITNESS_URL, f"URL mismatch: {loc.url!r} != {WITNESS_URL!r}"

    # /end/role/add reply should authorize the witness as its own controller.
    end = fresh_hby.db.ends.get(keys=(witness_pre, "controller", witness_pre))
    assert end is not None, "no /end/role/add reply persisted in db.ends"


def test_unknown_aid_returns_404(witness_pre):
    """Querying an OOBI for an AID the witness doesn't know returns 404."""
    fake_aid = "B" + "X" * 43  # well-formed-looking non-trans, definitely unknown
    try:
        http_get(f"/oobi/{fake_aid}/witness")
        pytest.fail("expected 404 for unknown AID")
    except urllib.error.HTTPError as e:
        assert e.code == 404, f"expected 404, got {e.code}"


def test_post_receipts_returns_signed_witness_receipt(fresh_hby, witness_pre):
    """A controller posts its inception; witness returns a verifiable receipt."""
    # 1. Resolve OOBI so we know the witness's verfer for later verification.
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    # 2. Build a transferable controller "bob" with the witness in his wits list.
    bob = fresh_hby.makeHab(
        name="bob", transferable=True,
        isith="1", icount=1, ncount=1, nsith="1",
        toad=1, wits=[witness_pre],
    )

    # 3. POST bob's inception to /receipts.
    status, headers, rct_bytes = http_post_cesr("/receipts", bob.makeOwnInception())
    assert status == 200
    assert headers["Content-Type"] == "application/cesr"
    assert b'"t":"rct"' in rct_bytes, (
        f"response is not a receipt: {rct_bytes[:120]!r}"
    )
    # Receipt should reference bob's AID and sn=0.
    assert bob.pre.encode() in rct_bytes
    assert b'"s":"0"' in rct_bytes

    # 4. Parse the receipt back; the witness signature must land in db.wigs.
    fresh_hby.psr.parse(ims=bytearray(rct_bytes))
    dgkey = (bob.pre.encode(), bob.kever.serder.saidb)
    wigs = fresh_hby.db.wigs.get(keys=dgkey)
    assert len(wigs) == 1, f"expected 1 wig, got {len(wigs)}"

    # 5. Cryptographically verify the signature against the witness's verfer.
    w = wigs[0]
    if w.verfer is None:
        w.verfer = Verfer(qb64=witness_pre)
    assert w.verfer.qb64 == witness_pre
    assert w.verfer.verify(sig=w.raw, ser=bob.kever.serder.raw), (
        "witness signature does not verify against bob's inception event"
    )


def test_post_root_returns_synchronous_witness_receipt(fresh_hby, witness_pre):
    """POST / (URL root) with an inception returns a CESR witness receipt.

    Regression guard for kerihost issue #2: standard keripy controllers
    (keri.app.agenting.WitnessReceiptor -> HTTPMessenger ->
    streamCESRRequests) POST events to the URL root declared in the
    witness's /loc/scheme reply, not to /receipts. They expect the
    witness's receipt in the HTTP response body. Previously this path
    generated and stored a receipt internally but returned 204 with an
    empty body, causing wallets to hang in "Waiting for witness
    receipts..." forever.
    """
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    bob = fresh_hby.makeHab(
        name="bob", transferable=True,
        isith="1", icount=1, ncount=1, nsith="1",
        toad=1, wits=[witness_pre],
    )

    status, headers, rct_bytes = http_post_cesr("/", bob.makeOwnInception())
    assert status == 200, f"POST / returned {status}, expected 200 with receipt body"
    assert headers["Content-Type"] == "application/cesr"
    assert b'"t":"rct"' in rct_bytes, (
        f"POST / response is not a receipt: {rct_bytes[:120]!r}"
    )
    assert bob.pre.encode() in rct_bytes
    assert b'"s":"0"' in rct_bytes

    # Receipt must parse cleanly and land in db.wigs
    fresh_hby.psr.parse(ims=bytearray(rct_bytes))
    dgkey = (bob.pre.encode(), bob.kever.serder.saidb)
    wigs = fresh_hby.db.wigs.get(keys=dgkey)
    assert len(wigs) == 1, f"expected 1 wig from POST /, got {len(wigs)}"


def test_post_root_handles_attachment_group_wrapper(fresh_hby, witness_pre):
    """POST / with CESR-ATTACHMENT wrapped in a -V AttachmentGroup counter
    returns the receipt instead of hanging.

    Regression guard for kerihost issue #4. Standard keripy clients
    (keri.app.agenting.streamCESRRequests) send the event JSON in the
    body and the attachments in the CESR-ATTACHMENT HTTP header. Many
    keripy releases wrap those header attachments in a -V
    AttachmentGroup counter (one of CESR's universal-with-override codes
    for "the next N quadlets are attachments for the preceding
    message"). Locksmith's wallet does this. Previously the witness
    handler called psr.parse without framed=True, and a -V wrapper
    whose declared quadlet count exceeded what the bounded ims actually
    contained caused the parser generator to yield forever waiting for
    bytes — the Lambda then hung until API Gateway timed out at 30s.
    """
    # We need Counter to build the -V wrapper; import locally so the test
    # file's earlier import block stays minimal.
    from keri.core.counting import Counter, Codens

    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    bob = fresh_hby.makeHab(
        name="bob", transferable=True,
        isith="1", icount=1, ncount=1, nsith="1",
        toad=1, wits=[witness_pre],
    )
    full = bytes(bob.makeOwnInception())

    # Split full CESR into body=event-JSON and raw_attach=trailing CESR
    nesting, end = 0, 0
    for i, b in enumerate(full):
        if b == 0x7B:
            nesting += 1
        elif b == 0x7D:
            nesting -= 1
            if nesting == 0:
                end = i + 1
                break
    body = full[:end]
    raw_attach = full[end:]

    # Wrap raw_attach in a -V AttachmentGroup counter, declaring
    # quadlet-count = len(raw_attach) / 4. This is the wire shape
    # Locksmith and other standard keripy clients send.
    quadlets = len(raw_attach) // 4
    wrapper = Counter(code=Codens.AttachmentGroup, count=quadlets).qb64b
    wrapped_attach = wrapper + raw_attach

    req = urllib.request.Request(
        f"{WITNESS_URL}/",
        data=body,
        headers={
            "Content-Type": "application/cesr",
            "Accept": "application/cesr",
            "CESR-ATTACHMENT": wrapped_attach.decode("utf-8"),
            "CESR-DESTINATION": witness_pre,
        },
        method="POST",
    )
    # Tight timeout — if the parser hangs, this test fails fast instead
    # of waiting on the API-Gateway 30s ceiling.
    with urllib.request.urlopen(req, timeout=15) as r:
        status = r.status
        headers = dict(r.headers)
        rct_bytes = r.read()

    assert status == 200, f"POST / with -V wrapper returned {status}, expected 200"
    assert headers["Content-Type"] == "application/cesr"
    assert b'"t":"rct"' in rct_bytes, (
        f"no receipt in response body: {rct_bytes[:120]!r}"
    )
    assert bob.pre.encode() in rct_bytes

    # Receipt parses cleanly and lands in db.wigs
    fresh_hby.psr.parse(ims=bytearray(rct_bytes))
    dgkey = (bob.pre.encode(), bob.kever.serder.saidb)
    wigs = fresh_hby.db.wigs.get(keys=dgkey)
    assert len(wigs) == 1, f"expected 1 wig after -V wrapped POST, got {len(wigs)}"


def test_post_empty_body_returns_400():
    """POSTing an empty body is rejected with a 400."""
    try:
        http_post_cesr("/receipts", b"")
        pytest.fail("expected 400 for empty body")
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"expected 400, got {e.code}"


def test_post_garbage_returns_error():
    """POSTing non-CESR garbage is rejected (4xx or 5xx, but not 200)."""
    try:
        status, _, body = http_post_cesr("/receipts", b"this is not CESR" * 10)
        # If the server is too lenient and returns 200, it must at least not
        # produce a receipt for nonsense input.
        assert b'"t":"rct"' not in body, (
            f"witness produced a receipt for garbage input: {body[:120]!r}"
        )
    except urllib.error.HTTPError as e:
        assert 400 <= e.code < 600, e.code


def test_post_receipts_kli_format(fresh_hby, witness_pre):
    """A controller using streamCESRRequests format gets a valid receipt back.

    This test will start passing once Phase 2 wires the CESR-ATTACHMENT
    header into the parser stream. It uses the same wire format that
    ``kli incept --receipt-endpoint``, signify-ts, and keria all produce.
    """
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    bob = fresh_hby.makeHab(
        name="bob", transferable=True,
        isith="1", icount=1, ncount=1, nsith="1",
        toad=1, wits=[witness_pre],
    )

    body, attachment = split_event_and_attachment(bob.makeOwnInception())
    assert len(body) > 0
    assert len(attachment) > 0
    assert attachment.startswith(b"-"), f"unexpected attachment prefix: {attachment[:10]!r}"

    req = urllib.request.Request(
        f"{WITNESS_URL}/receipts",
        data=body,
        headers={
            "Content-Type": "application/cesr",
            "Accept": "application/cesr",
            "CESR-ATTACHMENT": attachment.decode("utf-8"),
            "CESR-DESTINATION": witness_pre,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        rct_bytes = r.read()
    assert rct_bytes, f"witness returned empty body for kli-format POST"
    assert b'"t":"rct"' in rct_bytes, f"no receipt: {rct_bytes[:120]!r}"

    fresh_hby.psr.parse(ims=bytearray(rct_bytes))
    wigs = fresh_hby.db.wigs.get(keys=(bob.pre.encode(), bob.kever.serder.saidb))
    assert len(wigs) == 1


def test_get_receipts_after_post(fresh_hby, witness_pre):
    """After POSTing a controller's inception (kli format), GET /receipts
    returns the stored witness signatures.

    Verifies Phase 2 fix #4: handle_receipt_get must read db.wigs (where
    Kevery actually stores witness receipts) instead of db.rcts.
    """
    # Resolve witness OOBI so the local Habery knows the witness's keys.
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    # Build a fresh controller bob with the witness in his wits list.
    bob = fresh_hby.makeHab(
        name="bob", transferable=True,
        isith="1", icount=1, ncount=1, nsith="1",
        toad=1, wits=[witness_pre],
    )

    # POST bob's inception in kli/streamCESRRequests format.
    body, attachment = split_event_and_attachment(bob.makeOwnInception())
    req = urllib.request.Request(
        f"{WITNESS_URL}/receipts",
        data=body,
        headers={
            "Content-Type": "application/cesr",
            "Accept": "application/cesr",
            "CESR-ATTACHMENT": attachment.decode("utf-8"),
            "CESR-DESTINATION": witness_pre,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        assert r.status == 200, f"expected 200 from /receipts, got {r.status}"

    # GET /receipts?pre=...&sn=0 should now return witness_receipts >= 1.
    status, _, body_b = http_get(
        f"/receipts?pre={bob.pre}&sn=0", accept="application/json"
    )
    assert status == 200, f"expected 200 from GET /receipts, got {status}"
    data = json.loads(body_b)
    assert data["pre"] == bob.pre, data
    assert data["sn"] == 0, data
    assert data["witness_receipts"] >= 1, (
        f"expected >=1 witness receipt, got {data['witness_receipts']}"
    )
    assert data["witness_aid"] == witness_pre, data


def test_post_does_not_receipt_unrelated_aid(fresh_hby, witness_pre):
    """An inception that does not list the witness in wits should not
    get a witness receipt back.

    Verifies Phase 2 fix #3: _drain_receipt_cues skips cues whose pre's
    kever does not include hab.pre in its wits list.
    """
    # Resolve OOBI so the witness's KEL is in our local kevers (otherwise
    # the OOBI parse below would also skip).
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    # bob has NO witnesses (no wits, toad=0). The witness must refuse to
    # receipt his inception — its pre is not in bob.kever.wits = [].
    bob = fresh_hby.makeHab(
        name="bob", transferable=True,
        isith="1", icount=1, ncount=1, nsith="1",
    )
    assert bob.kever.wits == [], (
        f"test setup error: bob should have no wits, got {bob.kever.wits}"
    )

    status, _, body = http_post_cesr("/receipts", bob.makeOwnInception())
    # Either 204 (no receipt produced) is the expected success case; if
    # the witness does return a 200 it must NOT contain a receipt.
    assert status in (200, 204), f"expected 200 or 204, got {status}"
    assert b'"t":"rct"' not in body, (
        f"witness signed for an AID that does not list it as witness: "
        f"{body[:80]!r}"
    )


def test_oobi_advertises_mailbox_role(fresh_hby, witness_pre):
    """The witness's OOBI advertises Roles.mailbox alongside
    Roles.controller.

    Verifies Phase 3 init() additively registers the mailbox role.
    A KERI agent resolving the bare /oobi (default role) gets KEL +
    /loc/scheme + both /end/role/add records.
    """
    _, _, oobi = http_get("/oobi")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    assert witness_pre in fresh_hby.kevers, "witness KEL not registered"

    # Mailbox role authorization
    end_mbx = fresh_hby.db.ends.get(keys=(witness_pre, "mailbox", witness_pre))
    assert end_mbx is not None, (
        "no /end/role/add for role=mailbox in db.ends"
    )

    # Controller role still there too (Phase 1 regression guard)
    end_ctrl = fresh_hby.db.ends.get(keys=(witness_pre, "controller", witness_pre))
    assert end_ctrl is not None, (
        "controller-role authorization regressed"
    )


def test_oobi_witness_role_returns_witness_end_role(fresh_hby, witness_pre):
    """GET /oobi/<aid>/witness returns icp + /loc/scheme + /end/role/add(role=witness).

    Regression guard for kerihost issue #1: previously this endpoint
    returned only the inception event because init() never registered
    a witness-role end-role authorization. Without the witness-role
    entry in db.ends, a controller's WitnessReceiptor cannot find a
    route to publish inception events for receipting.
    """
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    assert witness_pre in fresh_hby.kevers, "witness KEL not registered"

    end_wit = fresh_hby.db.ends.get(keys=(witness_pre, "witness", witness_pre))
    assert end_wit is not None, (
        "no /end/role/add for role=witness in db.ends — "
        "WitnessReceiptor will have no route to publish events"
    )

    loc_https = fresh_hby.db.locs.get(keys=(witness_pre, "https"))
    loc_http = fresh_hby.db.locs.get(keys=(witness_pre, "http"))
    assert loc_https is not None or loc_http is not None, (
        "no /loc/scheme reply in OOBI"
    )


def test_post_fwd_stores_in_mailbox(fresh_hby, witness_pre):
    """Round-trip: alice POSTs /fwd to witness; bob POSTs qry r=/mbx
    and receives the message via SSE.

    Verifies:
      - Phase 3 ForwardHandler registration routes /fwd to
        Mailboxer.storeMsg.
      - Phase 3 handle_cesr_ingest branch returns SSE for qry r=/mbx.
      - Topic key construction matches between writer (ForwardHandler)
        and reader (_format_sse_events).
    """
    # Resolve the witness OOBI so our local hby knows its keys
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    # Alice (sender) and Bob (recipient) -- Bob's mailbox is the witness.
    alice = fresh_hby.makeHab(name="alice", transferable=True,
                              isith="1", icount=1, ncount=1, nsith="1")
    bob = fresh_hby.makeHab(name="bob", transferable=True,
                            isith="1", icount=1, ncount=1, nsith="1")

    # Publish alice's KEL to the witness so the Exchanger can verify her
    # signature on the /fwd exn. Without this the witness has no kever
    # for alice and silently drops the exn (Parser swallows the
    # MissingSignatureError at parsing.py:484).
    status_a, _, _ = http_post_cesr("/", alice.makeOwnInception())
    assert status_a == 204, f"alice icp publish failed: {status_a}"

    # Build a small embedded message -- bob's own inception will do for the
    # test. The witness doesn't validate the embed semantically; it just
    # stores the bytes.
    embedded = bob.makeOwnInception()

    # Alice forwards to Bob's /credential topic
    fwd_ims = _make_fwd_message(
        sender_hab=alice,
        recipient_pre=bob.pre,
        topic="/credential",
        embedded_msg=embedded,
    )
    status, _, body = http_post_cesr("/", fwd_ims)
    assert status == 204, f"expected 204 from /fwd POST, got {status}"

    # Bob polls his mailbox for the credential topic
    qry_ims = _make_mbx_query(
        querier_hab=bob,
        recipient_pre=bob.pre,
        topics={"/credential": -1},
        witness_pre=witness_pre,
    )
    status2, headers2, body2 = http_post_cesr("/", qry_ims)
    assert status2 == 200, f"expected 200 from qry r=/mbx, got {status2}"
    assert headers2.get("Content-Type") == "text/event-stream", (
        f"expected text/event-stream, got {headers2.get('Content-Type')!r}"
    )

    # SSE body should contain at least one event with the expected fields
    body2_text = body2.decode("utf-8") if isinstance(body2, (bytes, bytearray)) else body2
    assert "event: /credential" in body2_text, (
        f"missing event:/credential in SSE body: {body2_text[:200]!r}"
    )
    assert "id: 0" in body2_text, (
        f"missing id:0 (first message ordinal) in SSE body: {body2_text[:200]!r}"
    )


def test_mbx_query_empty_returns_sse_with_empty_body(fresh_hby, witness_pre):
    """A qry r=/mbx for an AID with no buffered messages returns
    `200 + text/event-stream + empty body`.

    Verifies that the SSE branch doesn't crash on empty topics.
    """
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    bob = fresh_hby.makeHab(name="bob", transferable=True,
                            isith="1", icount=1, ncount=1, nsith="1")

    qry_ims = _make_mbx_query(
        querier_hab=bob,
        recipient_pre=bob.pre,
        topics={"/credential": -1, "/receipt": -1},
        witness_pre=witness_pre,
    )
    status, headers, body = http_post_cesr("/", qry_ims)
    assert status == 200, f"expected 200, got {status}"
    assert headers.get("Content-Type") == "text/event-stream", (
        f"expected text/event-stream, got {headers.get('Content-Type')!r}"
    )
    body_text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body
    assert body_text == "", (
        f"expected empty SSE body, got {body_text[:200]!r}"
    )


def test_mbx_query_resumes_from_last_ordinal(fresh_hby, witness_pre):
    """Two messages stored under the same topic are retrievable in
    order, and the second can be fetched independently using the
    cursor ordinal.

    Verifies fn = last_seen + 1 cursor semantics in _format_sse_events.
    """
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    alice = fresh_hby.makeHab(name="alice", transferable=True,
                              isith="1", icount=1, ncount=1, nsith="1")
    bob = fresh_hby.makeHab(name="bob", transferable=True,
                            isith="1", icount=1, ncount=1, nsith="1")

    # Witness must know alice to verify her sigs on the /fwd exns.
    status_a, _, _ = http_post_cesr("/", alice.makeOwnInception())
    assert status_a == 204, f"alice icp publish failed: {status_a}"

    # Use two different embedded messages so we can tell them apart
    msg_a = bob.makeOwnInception()
    msg_b = bob.rotate()

    for embedded in (msg_a, msg_b):
        fwd = _make_fwd_message(alice, bob.pre, "/credential", embedded)
        status, _, _ = http_post_cesr("/", fwd)
        assert status == 204, f"/fwd POST returned {status}"

    # First poll: last_seen=-1 should return BOTH messages
    qry1 = _make_mbx_query(bob, bob.pre, {"/credential": -1},
                           witness_pre=witness_pre)
    s1, _, body1 = http_post_cesr("/", qry1)
    assert s1 == 200
    body1_text = body1.decode("utf-8") if isinstance(body1, (bytes, bytearray)) else body1
    assert "id: 0" in body1_text, f"missing id:0 in {body1_text[:200]!r}"
    assert "id: 1" in body1_text, f"missing id:1 in {body1_text[:200]!r}"

    # Second poll: last_seen=0 should return only the SECOND message
    qry2 = _make_mbx_query(bob, bob.pre, {"/credential": 0},
                           witness_pre=witness_pre)
    s2, _, body2 = http_post_cesr("/", qry2)
    assert s2 == 200
    body2_text = body2.decode("utf-8") if isinstance(body2, (bytes, bytearray)) else body2
    assert "id: 1" in body2_text, f"missing id:1 in {body2_text[:200]!r}"
    assert "id: 0" not in body2_text, (
        f"id:0 should not appear when last_seen=0: {body2_text[:200]!r}"
    )


def test_mbx_query_missing_q_pre_returns_400(fresh_hby, witness_pre):
    """A qry r=/mbx with `q.topics` but no `q.pre` returns 400 with
    the documented error.

    Verifies the validation guard inside handle_cesr_ingest's mbx
    branch.
    """
    _, _, oobi = http_get(f"/oobi/{witness_pre}/witness")
    fresh_hby.psr.parse(ims=bytearray(oobi))

    bob = fresh_hby.makeHab(name="bob", transferable=True,
                            isith="1", icount=1, ncount=1, nsith="1")

    # Build a signed qry r=mbx with q.topics but no q.pre. Hab.query
    # only sets q["i"] and q["src"], not q["pre"], so omitting pre
    # from the query dict produces exactly the malformed shape we want
    # to verify the handler rejects.
    qry = bytes(bob.query(
        pre=bob.pre,
        src=witness_pre,
        route="mbx",
        query={"topics": {"/credential": -1}},  # NOTE: no "pre"
    ))

    try:
        status, _, body = http_post_cesr("/", qry)
        # If urlopen returned without raising, we expect a 4xx body
        assert status == 400, f"expected 400, got {status}"
        data = json.loads(body) if body else {}
        assert "q.pre" in data.get("error", ""), (
            f"expected q.pre in error message: {data!r}"
        )
    except urllib.error.HTTPError as e:
        # urlopen raises on 4xx -- that's fine too
        assert e.code == 400, f"expected 400, got {e.code}"
        data = json.loads(e.read())
        assert "q.pre" in data.get("error", "")
