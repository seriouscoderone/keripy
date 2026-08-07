# -*- encoding: utf-8 -*-
"""The asking half of a watch: KEL sync + sealed-body retrieval.

Headless by construction — real habs, real signed messages, no transport and no
Qt. That is the point: the delivery leg of "the actuary sees the mandate by
watching" was previously reachable only through the GUI, so no test could hold
it.
"""
import json

import pytest
from keri.app import habbing

from keri_serviceaid.providers.peer_sync import (
    LOGS_ROUTE, SEALED_ROUTE, anchored_saids, body_request, kel_sync_request,
    missing_bodies,
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
