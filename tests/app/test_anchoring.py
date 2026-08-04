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
from keri.core import coring, eventing, parsing
from keri.core.signing import Salter
from keri.kering import Kinds


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


def test_a_repudiated_anchor_from_a_superseded_event_is_not_reported():
    """The compromise case. An attacker holding a stolen key anchors a digest in
    an ixn at sn=1; the controller recovers with a rotation at the SAME sn=1,
    which supersedes it. The ixn is still IN the database -- superseding does not
    delete, it forks -- but it is no longer part of the KEL the controller stands
    behind, so its anchor is repudiated and must not be reported. A watcher that
    reports it hands the consumer a digest that verifySealedBody will happily
    confirm a body against.

    The KEL is built with raw signers because superseding recovery cannot be
    driven through Hab (hab.rotate() advances to sn+1; it never re-issues at the
    current sn). This is a GENUINE superseding rotation, not a hand-written
    event: rotate() at sn=1 with dig pointing back to the icp, signed by the
    pre-committed next key, and it is validated by a real Kevery -- the same
    acceptance path any event from a peer takes. It is delivered over the real
    wire (Parser -> Kevery) into a watcher's own database, so the watcher sees
    exactly what a watcher sees.
    """
    salt = b'g\x15\x89\x1a@\xa4\xa47\x07\xb9Q\xb8\x18\xcdJW'
    signers = Salter(raw=salt).signers(count=3, transferable=True)

    icp = eventing.incept(keys=[signers[0].verfer.qb64],
                          ndigs=[coring.Diger(ser=signers[1].verfer.qb64b).qb64],
                          kind=Kinds.json)
    pre = icp.pre
    repudiated = coring.Diger(ser=b'{"body":"anchored-by-a-stolen-key"}').qb64
    ixn = eventing.interact(pre=pre, dig=icp.said, sn=1,
                            data=[{"d": repudiated}], kind=Kinds.json)
    rot = eventing.rotate(pre=pre, keys=[signers[1].verfer.qb64], dig=icp.said,
                          ndigs=[coring.Diger(ser=signers[2].verfer.qb64b).qb64],
                          sn=1, kind=Kinds.json)

    msgs = bytearray()
    msgs.extend(eventing.messagize(icp, sigers=[signers[0].sign(icp.raw, index=0)]))
    msgs.extend(eventing.messagize(ixn, sigers=[signers[0].sign(ixn.raw, index=0)]))
    msgs.extend(eventing.messagize(rot, sigers=[signers[1].sign(rot.raw, index=0)]))

    with habbing.openHby(name="recover", temp=True) as hby:
        watcher = hby.makeHab(name="watcher")
        kvy = eventing.Kevery(db=hby.db, lax=True, local=False)
        parsing.Parser(kvy=kvy).parse(ims=bytearray(msgs), kvy=kvy)

        # Premise checks: the recovery really was accepted, and the superseded
        # ixn really is still on disk. Without both, the assertion below could
        # pass because nothing arrived at all.
        assert hby.db.kels.getLast(keys=pre.encode(), on=1) == rot.said, \
            "the superseding rot was not accepted -- this test proves nothing"
        assert [(s.sn, s.ilk) for s in hby.db.getEvtPreIter(pre=pre)] == \
            [(0, "icp"), (1, "ixn"), (1, "rot")], \
            "the superseded ixn is not in the database -- premise changed"

        from keri.app.anchoring import AnchorWatcher
        reported = AnchorWatcher(hab=watcher, pre=pre).since(sn=0)

        assert repudiated not in [s["d"] for _, s in reported], \
            "an anchor from a SUPERSEDED event was reported"
        assert reported == []


def test_seals_that_are_not_digest_anchors_are_skipped():
    """A KEL may legally carry seals with no `d` at all, and `a` entries that
    are not mappings. Every caller of since() does seal["d"], so anything the
    watcher yields must have one. Anchoring a delegation seal or a registry-root
    seal must not make a watcher raise on the next poll.
    """
    with habbing.openHby(name="mixed", temp=True) as hby:
        peer = hby.makeHab(name="peer")
        digest = coring.Diger(ser=b'{"n":"real"}').qb64

        peer.interact(data=[{"i": peer.pre}])            # SealLast/SealBack
        peer.interact(data=[{"rd": "EAAA" * 11}])        # SealRoot
        peer.interact(data=["not-even-a-mapping", 42])   # hab.interact accepts these
        peer.interact(data=[{"d": digest}])              # the one real anchor

        from keri.app.anchoring import AnchorWatcher
        watcher = AnchorWatcher(hab=peer, pre=peer.pre)
        found = watcher.since(sn=0)

        assert [s["d"] for _, s in found] == [digest]    # the documented caller pattern
        assert [n for n, _ in found] == [4]
        assert watcher.checkpoint == 4                   # the others WERE examined


def test_a_negative_checkpoint_scans_the_whole_kel():
    """.checkpoint starts at -1, so `since(sn=watcher.checkpoint)` on the very
    first poll passes -1. That must mean "everything", not an error."""
    with habbing.openHby(name="fresh", temp=True) as hby:
        peer = hby.makeHab(name="peer")
        said = coring.Diger(ser=b'{"n":"first"}').qb64
        peer.interact(data=[{"d": said}])

        from keri.app.anchoring import AnchorWatcher
        watcher = AnchorWatcher(hab=peer, pre=peer.pre)
        assert watcher.checkpoint == -1
        assert [s["d"] for _, s in watcher.since(sn=watcher.checkpoint)] == [said]
