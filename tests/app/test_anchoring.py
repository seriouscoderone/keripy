# -*- encoding: utf-8 -*-
"""Anchor-watching tests.

SEAL TYPE DECISION (Task 1, measured 2026-08-03): anchors in this system are
SealDigest — a single-key dict {"d": <said>} — produced by
hab.interact(data=[{"d": said}]). Chosen because it is the minimum that commits
to a body, requires no knowledge of the anchoring event's own sequence number at
anchor time, and is locatable via Baser.fetchLastSealingEventBySeal on this fork.
SealEvent remains locatable and is not forbidden; nothing in this plan needs it.
"""
from keri.app import habbing
from keri.core import coring


def test_which_seal_shapes_the_finder_can_locate():
    """Pins the seal-type decision. Both shapes are anchored; the general
    finder must locate both on this fork (finding 59 fixed at d09ca318)."""
    with habbing.openHby(name="seal", temp=True) as hby:
        hab = hby.makeHab(name="anchorer")

        digest_said = coring.Diger(ser=b'{"kind":"digest-anchored"}').qb64
        hab.interact(data=[{"d": digest_said}])

        event_seal = dict(i=hab.pre, s=coring.Number(num=1).numh, d=hab.kever.serder.said)
        hab.interact(data=[event_seal])

        found_digest = hab.db.fetchLastSealingEventBySeal(pre=hab.pre, seal={"d": digest_said})
        found_event = hab.db.fetchLastSealingEventBySeal(pre=hab.pre, seal=event_seal)

        assert found_digest is not None, "SealDigest must be locatable on this fork"
        assert found_event is not None, "SealEvent must be locatable"


def test_watcher_reports_only_anchors_newer_than_the_checkpoint():
    with habbing.openHby(name="watch", temp=True) as hby:
        peer = hby.makeHab(name="peer")
        first = coring.Diger(ser=b'{"n":1}').qb64
        second = coring.Diger(ser=b'{"n":2}').qb64
        peer.interact(data=[{"d": first}])
        peer.interact(data=[{"d": second}])

        from keri.app.anchoring import AnchorWatcher
        watcher = AnchorWatcher(hab=peer, pre=peer.pre)

        found = watcher.since(sn=0)
        assert [seal["d"] for _, seal in found] == [first, second]
        assert watcher.checkpoint == 2

        assert watcher.since(sn=watcher.checkpoint) == []


def test_watcher_skips_events_with_no_seals():
    with habbing.openHby(name="noseal", temp=True) as hby:
        peer = hby.makeHab(name="peer")
        peer.interact()                                  # ixn, no anchors
        said = coring.Diger(ser=b'{"n":3}').qb64
        peer.interact(data=[{"d": said}])
        from keri.app.anchoring import AnchorWatcher
        watcher = AnchorWatcher(hab=peer, pre=peer.pre)
        assert [s["d"] for _, s in watcher.since(sn=0)] == [said]


def test_watcher_reports_every_seal_in_a_multi_seal_event():
    with habbing.openHby(name="multi", temp=True) as hby:
        peer = hby.makeHab(name="peer")
        a = coring.Diger(ser=b'{"n":"a"}').qb64
        b = coring.Diger(ser=b'{"n":"b"}').qb64
        peer.interact(data=[{"d": a}, {"d": b}])
        from keri.app.anchoring import AnchorWatcher
        watcher = AnchorWatcher(hab=peer, pre=peer.pre)
        assert [s["d"] for _, s in watcher.since(sn=0)] == [a, b]


def test_checkpoint_is_a_scan_cursor_not_the_last_reported_seal():
    """A trailing event with no seals still advances the checkpoint.

    checkpoint is the highest sn *examined*, not the highest sn *returned*.
    Without this, a peer whose latest event carries no anchors would be
    re-scanned on every poll forever.
    """
    with habbing.openHby(name="cursor", temp=True) as hby:
        peer = hby.makeHab(name="peer")
        said = coring.Diger(ser=b'{"n":"only"}').qb64
        peer.interact(data=[{"d": said}])            # sn=1, anchored
        peer.interact()                              # sn=2, no seals

        from keri.app.anchoring import AnchorWatcher
        watcher = AnchorWatcher(hab=peer, pre=peer.pre)

        found = watcher.since(sn=0)
        assert [s["d"] for _, s in found] == [said]
        assert [n for n, _ in found] == [1]          # only sn 1 carried a seal
        assert watcher.checkpoint == 2               # but sn 2 WAS examined

        assert watcher.since(sn=watcher.checkpoint) == []
