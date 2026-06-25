"""handle_receipt_post receipt-on-held: a witness re-serves its receipt for an
already-held, non-duplicitous event (200) on re-request, matching canonical
ReceiptEnd.on_post — not 204. Plus the duplicity guard (400) the fork adds."""
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")

from keri_cdk.handlers.witness import witness_handler
from keri.app import habbing
# Reuse the established moto cold-start harness.
from tests.handlers.test_witness_keeper import (
    _create_baser_table, _set_env, _reset_singletons,
)


def _event(cesr: bytes) -> dict:
    # Inline-body wire format (the path _extract_cesr_stream documents for
    # pytest fixtures): full CESR (event + attachments) in the body.
    return {"body": bytes(cesr).decode("utf-8"), "headers": {}}


def _booted_witness(monkeypatch):
    _set_env(monkeypatch)
    _create_baser_table()
    _reset_singletons(witness_handler)
    witness_handler.init()
    return witness_handler._hab.pre


def _controller_icp(wit_pre: str) -> bytes:
    # A controller witnessed by wit_pre (toad=1); its own signed icp.
    # salt must be a valid CESR qb64 string (not raw bytes).
    from keri.core.signing import Salter
    salt = Salter(raw=b'0123456789abcdef').qb64  # deterministic from raw bytes
    with habbing.openHby(name="ctrl", temp=True, salt=salt) as hby:
        hab = hby.makeHab(name="ctrl", wits=[wit_pre], toad=1, transferable=True)
        return bytes(hab.msgOwnEvent(sn=0, framed=True))


@needs_moto
def test_reserves_receipt_for_held_event(monkeypatch):
    with mock_aws():
        wit = _booted_witness(monkeypatch)
        icp = _controller_icp(wit)

        # POST #1 — first-seen accept: 200 + receipt (existing cue path).
        r1 = witness_handler.handle_receipt_post(_event(icp))
        assert r1["statusCode"] == 200 and r1["body"]

        # POST #2 — event now HELD, re-request: 200 + receipt (THE FIX; was 204).
        r2 = witness_handler.handle_receipt_post(_event(icp))
        assert r2["statusCode"] == 200 and r2["body"]


@needs_moto
def test_refuses_duplicitous_event_at_existing_sn(monkeypatch):
    with mock_aws():
        wit = _booted_witness(monkeypatch)
        icp = _controller_icp(wit)
        witness_handler.handle_receipt_post(_event(icp))  # witness now holds sn=0

        # Simulate a conflicting event at the held sn: force kels.getLast to
        # report a DIFFERENT first-seen said than the inbound event carries.
        monkeypatch.setattr(
            witness_handler._hby.db.kels, "getLast",
            lambda *a, **k: "EdifferentSaidAtThisSnXXXXXXXXXXXXXXXXXXXXXXX",
        )
        r = witness_handler.handle_receipt_post(_event(icp))
        assert r["statusCode"] == 400


@needs_moto
def test_202_when_kel_not_held(monkeypatch):
    with mock_aws():
        wit = _booted_witness(monkeypatch)
        icp = _controller_icp(wit)
        # Force "not held": stub out the cue path so the first-seen path
        # produces no receipt, AND stub kels.getLast to report no held event.
        # This ensures we reach the fallback and take the 202 branch.
        monkeypatch.setattr(
            witness_handler, "_drain_receipt_cues",
            lambda *a, **k: bytearray(),
        )
        monkeypatch.setattr(
            witness_handler._hby.db.kels, "getLast",
            lambda *a, **k: None,
        )
        r = witness_handler.handle_receipt_post(_event(icp))
        assert r["statusCode"] == 202
