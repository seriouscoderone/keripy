# -*- encoding: utf-8 -*-
"""The asking half of a watch: KEL sync + sealed-body retrieval.

Headless by construction — real habs, real signed messages, no transport and no
Qt. That is the point: the delivery leg of "the actuary sees the mandate by
watching" was previously reachable only through the GUI, so no test could hold
it.
"""
import json

import pytest
from keri import kering
from keri.app import habbing
from keri.core import eventing

from keri_serviceaid.providers.peer_sync import (
    LOGS_ROUTE, SEALED_ROUTE, anchored_saids, body_request, kel_sync_request,
    missing_bodies, signing_hab,
)


def _ked(raw: bytes) -> dict:
    """The JSON head of a signed KERI message (the CESR attachments follow)."""
    text = raw.decode("utf-8", errors="replace")
    depth = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[: i + 1])
    raise AssertionError("no complete JSON object in message")


def test_kel_sync_request_asks_the_peer_about_the_peer():
    """`pre` and `src` are both the peer: we ask that identifier, about that
    identifier. Getting this backwards produces a query the answering Kevery
    silently escrows as not-found rather than replaying."""
    with habbing.openHby(name="psync1", temp=True) as hby:
        asker, peer = hby.makeHab(name="asker"), hby.makeHab(name="peer")
        ked = _ked(kel_sync_request(asker, peer.pre))

        assert ked["t"] == "qry"
        assert ked["r"] == LOGS_ROUTE          # bare "logs" — processQuery's branch
        assert ked["q"]["i"] == peer.pre
        assert ked["q"]["src"] == peer.pre
        assert ked["i"] == asker.pre           # signed BY the asker


def test_the_request_is_signed_by_the_asking_hab():
    """An unsigned query is not attributable, and the answering side is within
    its rights to drop it. Assert the signature material is actually attached
    rather than trusting that `endorse` was called."""
    with habbing.openHby(name="psync2", temp=True) as hby:
        asker, peer = hby.makeHab(name="asker"), hby.makeHab(name="peer")
        raw = kel_sync_request(asker, peer.pre)
        head = json.dumps(_ked(raw), separators=(",", ":")).encode()
        assert len(raw) > len(head), "no attachments — the query is unsigned"


def test_body_request_is_a_sealed_route_prod():
    with habbing.openHby(name="psync3", temp=True) as hby:
        asker = hby.makeHab(name="asker")
        said = "E" + "Z" * 43
        ked = _ked(body_request(asker, said=said))

        assert ked["t"] == "pro"
        assert ked["r"] == SEALED_ROUTE
        assert said in json.dumps(ked)


def test_missing_bodies_returns_only_what_is_absent():
    """The filter exists so a poller asks for what it lacks instead of
    re-requesting every tick."""
    present = "E" + "A" * 43
    absent = "E" + "B" * 43

    class _Creds:
        def get(self, keys):
            return object() if keys[0] == present else None

    class _Reger:
        creds = _Creds()

    assert missing_bodies(_Reger(), [present, absent]) == [absent]
    assert missing_bodies(_Reger(), [present]) == []
    assert missing_bodies(_Reger(), ["", None]) == []


def test_missing_bodies_treats_an_unreadable_registry_as_unknown():
    """Fail toward asking. Treating a read error as "already have it" would
    silently stop retrieval for that SAID forever."""
    class _Boom:
        def get(self, keys):
            raise RuntimeError("registry closed")

    class _Reger:
        creds = _Boom()

    said = "E" + "C" * 43
    assert missing_bodies(_Reger(), [said]) == [said]


def test_anchored_saids_reads_the_seal_i_field():
    class _Watcher:
        checkpoint = 0

        def since(self, _since):
            return [(1, {"i": "E" + "D" * 43}), (2, {}), (3, {"i": "E" + "E" * 43})]

    assert anchored_saids(_Watcher()) == ["E" + "D" * 43, "E" + "E" * 43]


def test_anchored_saids_honours_an_explicit_since():
    seen = {}

    class _Watcher:
        checkpoint = 7

        def since(self, since):
            seen["since"] = since
            return []

    anchored_saids(_Watcher(), since=3)
    assert seen["since"] == 3          # explicit wins
    anchored_saids(_Watcher())
    assert seen["since"] == 7          # falls back to the checkpoint


# ---------------------------------------------------------------------------
# The test the seven above did not write: does a RECEIVER accept the message?
# ---------------------------------------------------------------------------

def _receiver(hby, pin):
    """A parser built the way locksmith's direct-mode Reactant builds one
    (turret/directing.py:566-572): framed, version-pinned, real Kevery."""
    from keri.core import parsing, routing

    rvy = routing.Revery(db=hby.db)
    kvy = eventing.Kevery(db=hby.db, lax=True, local=False, rvy=rvy)
    return kvy, parsing.Parser(kvy=kvy, rvy=rvy, framed=True, version=pin)


def _deliver(parser, kvy, raw):
    """Parse `raw` and return (replay_cues, parser_errors).

    Reads the PARSER'S LOG, not its return value, because a failed parse in
    keripy is indistinguishable from a successful one by return value alone:
    `parsator` catches ExtractionError, logs it via hio's ogler, and then does
    `del ims[:]` (parsing.py:776-781). So a broken message leaves NO exception
    and leftover == 0 -- exactly what success leaves. Asserting on those two
    signals is how a completely dead sync passed seven tests and a manual
    probe. The only honest tells are the log record and the cue.
    """
    import logging
    from keri.core import parsing

    seen = []

    class _Capture(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    handler = _Capture()
    parsing.logger.addHandler(handler)
    prior = parsing.logger.level
    parsing.logger.setLevel(logging.ERROR)
    try:
        parser.parse(ims=bytearray(raw), kvy=kvy)
    finally:
        parsing.logger.removeHandler(handler)
        parsing.logger.setLevel(prior)

    errors = [m for m in seen if "extraction error" in m or "GroupParsator" in m]
    replays = [c for c in kvy.cues if c.get("kin") == "replay"]
    return replays, errors


def test_a_v1_receiver_accepts_the_query_and_replays():
    """End-to-end against the live configuration: v1 identifiers (locksmith
    pins Vrsn_1_0 -- core/habbing.py:432) talking to a v1-pinned parser.

    Guards the defect this feature shipped with. `eventing.query`'s `version`
    defaults to the module-level Version (v2) no matter what the hab speaks,
    so the query went out v2, and the v1-pinned receiver rejected it at
    serdering.py:196 -- every one of them, before `processQuery` was reached.
    On the wire that produced "Parser msg extraction error:" with an empty
    message and a sync that did precisely nothing.
    """
    with habbing.openHby(name="rx1", temp=True) as hby:
        asker = hby.makeHab(name="asker", version=kering.Vrsn_1_0)
        target = hby.makeHab(name="target", version=kering.Vrsn_1_0)

        raw = kel_sync_request(asker, target.pre)
        kvy, parser = _receiver(hby, kering.Vrsn_1_0)
        replays, errors = _deliver(parser, kvy, raw)

        assert not errors, f"receiver rejected the query: {errors}"
        assert replays, "no replay cue — the query was dropped before processQuery"
        assert replays[0]["pre"] == target.pre


def test_the_query_speaks_the_signing_habs_own_version():
    """The invariant behind the fix: ask in the version you are.

    Pinning to a literal would bake locksmith's TRANSITIONAL v1 into a
    protocol-neutral library; deriving from the hab means a v1 identifier
    asks in v1 and a v2 identifier asks in v2.
    """
    with habbing.openHby(name="rx2", temp=True) as hby:
        for vrsn in (kering.Vrsn_1_0, kering.Vrsn_2_0):
            hab = hby.makeHab(name=f"h{vrsn.major}", version=vrsn)
            ked = _ked(kel_sync_request(hab, hab.pre))
            assert ked["v"].startswith("KERI"), ked["v"]
            expected = "KERI10JSON" if vrsn.major == 1 else "KERICAAC"
            assert ked["v"].startswith(expected), (
                f"hab speaks v{vrsn.major} but its query says {ked['v']}")


def test_signing_hab_never_returns_a_non_transferable_identifier():
    """A non-transferable signer attaches cigars, and keripy's msgProcess then
    reads `exts['source']` (parsing.py:1526) -- a key MsgParseDom does not
    declare and only the `lsgs` branch sets. The query dies with
    KeyError('source') before processQuery, silently, at INFO.

    So this is a protocol requirement, not a preference: locksmith's habs dict
    holds the non-transferable "peer-listener" EID, and picking it produced a
    sync that sent perfectly well-formed queries into a hole.
    """
    with habbing.openHby(name="rx3", temp=True) as hby:
        listener = hby.makeHab(name="peer-listener", transferable=False,
                               version=kering.Vrsn_1_0)
        assert not listener.kever.prefixer.transferable

        # Only a non-transferable hab exists -> refuse rather than sign badly.
        assert signing_hab(hby, "peer-listener") is None
        assert signing_hab(hby, None) is None

        user = hby.makeHab(name="default", version=kering.Vrsn_1_0)
        assert signing_hab(hby, "default") is user
        # Asked for the listener by name, it still refuses it.
        assert signing_hab(hby, "peer-listener") is user
