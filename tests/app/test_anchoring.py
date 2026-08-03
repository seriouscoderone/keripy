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
