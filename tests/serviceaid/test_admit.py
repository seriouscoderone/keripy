"""Live-stack test for the single-sig IPEX admit lift (Task 7).

Two real, separate Haberies (granter, admitter) exercise the full cross-party
IPEX grant -> admit round trip. The granter self-issues + grants a minimal
ACDC via `issue_credential` + `frame_grant_for(..., return_raw=True)` (the
same host-agnostic halves `self_issue_and_grant`, Task 6, composes — see the
`delivered_grant` fixture below for why this test calls them directly rather
than through that public wrapper); the "delivery" step then feeds the
admitter exactly what a real transport would ahead of/alongside the grant —
the granter's full KEL and the registry's full TEL (the same artifacts
`credentialing.sendArtifacts` streams over the wire in production, see
`keri/vdr/credentialing.py::sendArtifacts`) — plus the raw grant exn CESR
stream itself, before `admit_grant` (this task's produced function) is
called. This directory's autouse `_hold_serviceaid_v1` conftest fixture pins
every hab incepted here to v1 (see conftest.py's module docstring).
"""
from types import SimpleNamespace

import pytest
from keri.app.habbing import Habery
from keri.app.notifying import Notifier
from keri.core import parsing, scheming
from keri.core.signing import Salter
from keri.kering import Kinds, Vrsn_1_0
from keri.peer import exchanging
from keri.vc import protocoling
from keri.vdr import credentialing, verifying
from keri.vdr import eventing as teventing

from _schema import RATING_SCHEMA_SAD

from keri_serviceaid.envelope import envelope_for
from keri_serviceaid.providers.admit import admit_grant
from keri_serviceaid.providers.issue import frame_grant_for, issue_credential


class Capture:
    def __init__(self):
        self.events = []

    def on_event(self, source, event_type, data):
        self.events.append((source, event_type, data))


def _make_party(name, salt):
    hby = Habery(name=name, temp=True, salt=Salter(raw=salt).qb64)
    hab = hby.makeHab(name=name, transferable=True)
    rgy = credentialing.Regery(hby=hby, name=name, temp=True)
    return hby, hab, rgy


def _introduce_kel(dst_hby, src_hab):
    """Mocked OOBI resolution: feed src_hab's full KEL into dst_hby's own
    Kevery so dst recognizes src as a known counterparty (mirrors this
    directory's `recipient_pre` fixture in conftest.py)."""
    parsing.Parser(kvy=dst_hby.kvy, version=Vrsn_1_0).parse(
        ims=bytearray(src_hab.replay()))
    dst_hby.kvy.processEscrows()


@pytest.fixture
def granter_env():
    hby, hab, rgy = _make_party("grt-admit", b'0granter-salt-01')
    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)
    hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    yield SimpleNamespace(hby=hby, hab=hab, rgy=rgy, schemer=schemer)
    hby.close()


@pytest.fixture
def admitter_env():
    hby, hab, rgy = _make_party("adm-admit", b'0admitter-salt01')
    kvy = hby.kvy
    tvy = teventing.Tevery(db=hby.db, reger=rgy.reger)
    vry = verifying.Verifier(hby=hby, reger=rgy.reger)
    notifier = Notifier(hby)
    exc = exchanging.Exchanger(hby=hby, handlers=[])
    protocoling.loadHandlers(hby, exc=exc, notifier=notifier)
    yield SimpleNamespace(hby=hby, hab=hab, rgy=rgy, kvy=kvy, tvy=tvy, vry=vry, exc=exc)
    hby.close()


@pytest.fixture
def delivered_grant(granter_env, admitter_env):
    """Granter self-issues + grants a minimal ACDC to the admitter, then
    "delivers" it. Returns (grant_said, credential_said)."""
    # Schema resolution is a precondition on each party's own Verifier —
    # register it on both sides (mirrors real schema-OOBI resolution).
    admitter_env.hby.db.schema.pin(
        keys=(granter_env.schemer.said,), val=granter_env.schemer)

    # Cross-introduce identities (mocked OOBI), both directions: the
    # admitter's KEL must be known to the granter before `create()` can
    # issue TO it (conftest.py's `recipient_pre` fixture does the same); the
    # granter's KEL must be known to the admitter so the exn's signature
    # verifies and the `anc` embed's anchor resolves.
    _introduce_kel(granter_env.hby, admitter_env.hab)
    _introduce_kel(admitter_env.hby, granter_env.hab)

    sink = Capture()
    timestamp = "2026-07-16T00:00:00.000000+00:00"
    # `self_issue_and_grant` composes `issue_credential` + `frame_grant_for`,
    # but `frame_grant_for` has no timestamp override for the grant exn's own
    # `dt` (it always stamps `helping.nowIso8601()`), so calling
    # `issue_credential` directly (with an explicit `timestamp`, for a
    # deterministic credential `dt`) followed by `frame_grant_for(...,
    # return_raw=True)` is equivalent to the composed call here — and gets
    # the raw framed bytes back directly from the public API (Task B0),
    # dissolving the previous coupling to the private `_frame_grant` helper.
    credential_said = issue_credential(
        granter_env.hby, granter_env.hab, granter_env.rgy,
        schema_said=granter_env.schemer.said, recipient=admitter_env.hab.pre,
        attributes={"score": 1}, registry_name="grt-admit", sink=sink,
        timestamp=timestamp)
    grant_said, msg = frame_grant_for(
        granter_env.hby, granter_env.hab, granter_env.rgy,
        credential_said=credential_said, recipient=admitter_env.hab.pre,
        sink=sink, return_raw=True)

    # "Deliver" the granter's KEL + registry TEL artifacts — the same
    # payload credentialing.sendArtifacts streams over the wire ahead of a
    # real grant — then the grant exn itself. The registry's own inception
    # (`vcp`) lives under the registry's own pre (regk); the credential's
    # issuance (`iss`) is a SEPARATE one-event log keyed by the credential's
    # own SAID (see sendArtifacts's two clonePreIter calls at
    # keri/vdr/credentialing.py:1092 and :1097).
    regk = granter_env.rgy.registryByName("grt-admit").regk
    for kel_msg in granter_env.hby.db.clonePreIter(pre=granter_env.hab.pre):
        parsing.Parser(kvy=admitter_env.kvy, version=Vrsn_1_0).parseOne(
            ims=bytearray(kel_msg))
    admitter_env.kvy.processEscrows()

    for tel_msg in granter_env.rgy.reger.clonePreIter(pre=regk):
        parsing.Parser(tvy=admitter_env.tvy, version=Vrsn_1_0).parseOne(
            ims=bytearray(tel_msg))
    for tel_msg in granter_env.rgy.reger.clonePreIter(pre=credential_said):
        parsing.Parser(tvy=admitter_env.tvy, version=Vrsn_1_0).parseOne(
            ims=bytearray(tel_msg))

    parsing.Parser().parseOne(ims=bytes(msg), exc=admitter_env.exc,
                              version=Vrsn_1_0)

    return grant_said, credential_said


@pytest.fixture
def delivered_grant_said(delivered_grant):
    return delivered_grant[0]


@pytest.fixture
def delivered_credential_said(delivered_grant):
    return delivered_grant[1]


def test_admit_saves_credential_and_returns_admit_said(
        admitter_env, delivered_grant_said, delivered_credential_said):
    admit_said = admit_grant(
        admitter_env.hby, admitter_env.hab, admitter_env.rgy,
        grant_said=delivered_grant_said,
        kvy=admitter_env.kvy, tvy=admitter_env.tvy, vry=admitter_env.vry)

    assert admit_said.startswith("E")
    assert admitter_env.rgy.reger.saved.get(
        keys=(delivered_credential_said,)) is not None


def test_admit_emits_sink_event_on_success(
        admitter_env, delivered_grant_said, delivered_credential_said):
    sink = Capture()
    admit_grant(
        admitter_env.hby, admitter_env.hab, admitter_env.rgy,
        grant_said=delivered_grant_said,
        kvy=admitter_env.kvy, tvy=admitter_env.tvy, vry=admitter_env.vry,
        sink=sink)

    # The admitted ACDC, read the same way admit_grant itself reads it (the
    # grant exn's own "acdc" embed) — so the expected envelope below is
    # derived from the real admitted credential, not retyped by hand.
    grant, _ = exchanging.cloneMessage(admitter_env.hby, delivered_grant_said)
    acdc = grant.ked["e"]["acdc"]

    assert ("AdmitDoer", "admit_complete",
           {"success": True, "credential_said": delivered_credential_said,
            "envelope": envelope_for(acdc, verified=True)}) in sink.events


def test_admit_defaults_processors_when_omitted(
        admitter_env, delivered_grant_said, delivered_credential_said):
    """Verify that admit_grant works without explicit kvy/tvy/vry (regression for 20325281).

    Commit 20325281 added defaulting so that when kvy/tvy/vry are omitted, the grant's
    embeds are properly verified into the local stores. This test mirrors the successful
    admission path but exercises the default path (no kvy/tvy/vry kwargs) to ensure the
    credential actually saves — the production path locksmith uses.
    """
    admit_said = admit_grant(
        admitter_env.hby, admitter_env.hab, admitter_env.rgy,
        grant_said=delivered_grant_said)

    assert admit_said.startswith("E")
    assert admitter_env.rgy.reger.saved.get(
        keys=(delivered_credential_said,)) is not None


def test_multisig_hab_raises_not_implemented(
        admitter_env, delivered_grant_said, monkeypatch):
    monkeypatch.setattr(
        admitter_env.hab, "__class__",
        type("GroupHab", (admitter_env.hab.__class__,), {}))

    with pytest.raises(NotImplementedError):
        admit_grant(admitter_env.hby, admitter_env.hab, admitter_env.rgy,
                    grant_said=delivered_grant_said)
