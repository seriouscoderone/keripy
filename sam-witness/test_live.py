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


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_status_endpoint_returns_witness_metadata():
    """GET / returns JSON with witness AID and identifier metadata."""
    status, headers, body = http_get("/", accept="application/json")
    assert status == 200
    data = json.loads(body)
    assert data["witness"].startswith("B"), data
    # Aliases vary per pool member (e.g. "witness", "witness-legitim",
    # "witness-goonei"). All start with "witness".
    assert data["alias"].startswith("witness"), data
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
    """A controller posts its inception; witness returns a verifiable receipt.

    Regression guard for kerihost issues #3 and #7. The witness must:
      - Accept the inception event (controller signature is the only
        cryptographic gate at first-seen)
      - First-see it (not park in misfit/partial-witness escrow on the
        grounds that the witness is "locally witnessed" — see #7 for
        why a non-local lax Kevery breaks this; we use _hby.psr instead)
      - Issue and return its own signed receipt CESR in the response body

    If this test ever asserts on a 204 / empty body, the witness handler's
    Kevery configuration has regressed (likely the local/lax flags).
    """
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
    status, headers, rct_bytes = http_post_cesr("/receipts", bob.msgOwnInception())
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

    status, headers, rct_bytes = http_post_cesr("/", bob.msgOwnInception())
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
    full = bytes(bob.msgOwnInception())

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

    body, attachment = split_event_and_attachment(bob.msgOwnInception())
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
    body, attachment = split_event_and_attachment(bob.msgOwnInception())
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

    status, _, body = http_post_cesr("/receipts", bob.msgOwnInception())
    # Either 204 (no receipt produced) is the expected success case; if
    # the witness does return a 200 it must NOT contain a receipt.
    assert status in (200, 204), f"expected 200 or 204, got {status}"
    assert b'"t":"rct"' not in body, (
        f"witness signed for an AID that does not list it as witness: "
        f"{body[:80]!r}"
    )


def test_oobi_does_not_advertise_mailbox_role(fresh_hby, witness_pre):
    """The stripped witness must NOT advertise role=mailbox.

    Regression guard for the role split: mailbox is served by the
    separate sam-mailbox stack now. Issue #6/#7-adjacent: we publish
    /end/role/cut for the historical mailbox-role rpy on cold-start to
    retire any prior add. The OOBI reply must reflect that.
    """
    _, _, oobi = http_get("/oobi")
    assert b'"role":"mailbox"' not in oobi, (
        "witness still advertises mailbox role after strip — "
        "/end/role/cut on cold-start may not have published"
    )

    fresh_hby.psr.parse(ims=bytearray(oobi))
    assert witness_pre in fresh_hby.kevers, "witness KEL not registered"

    # Controller + witness roles still present (positive regression guard)
    end_ctrl = fresh_hby.db.ends.get(keys=(witness_pre, "controller", witness_pre))
    assert end_ctrl is not None, "controller-role authorization regressed"


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

