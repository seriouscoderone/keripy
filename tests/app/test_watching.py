"""Anchor-watching tests.

SEAL TYPE DECISION (Task 1, measured 2026-08-03): anchors in this system are
SealDigest — a single-key dict {"d": <said>} — produced by
hab.interact(data=[{"d": said}]). Chosen because it is the minimum that commits
to a body, requires no knowledge of the anchoring event's own sequence number at
anchor time, and is locatable via Baser.fetchLastSealingEventBySeal on this fork.
SealEvent remains locatable and is not forbidden; nothing in this plan needs it.
"""
from dataclasses import asdict

import pytest

from keri.app import Adjudicator, DiffState, diffState, habbing, openHby
from keri.core import Saider, Salter, coring
from keri.recording import KeyStateRecord, ObservedRecord

from tests.common import CUE_KWA, KWA


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


def test_diffstate():
    d0 = {'vn': [1, 0],
          'i': 'EZ-i0d8JZAoTNZH3ULaU6JR2nmwyvYAfSVPzhzS6b5CM',
          's': '0',
          'p': 'ElsHFkbZQjRb7xHnuE-wyiarIZ9j-1CEQ89I0E3WevcE',
          'd': 'EBiIFxr_o1b4x1YR21PblAFpFG61qDghqFBDyVSOXYW0',
          'f': '0',
          'dt': '2021-06-09T17:35:54.169967+00:00',
          'et': '2021-06-09T17:35:54.169967+00:00',
          'kt': '1',
          'k': ["D-HwiqmaETxls3vAVSh0xpXYTs94NUJX6juupWj_EgsA"],
          'nt': '1',
          'n': ["ED6lKZwg-BWl_jlCrjosQkOEhqKD4BJnlqYqWmhqPhaU"],
          'bt': '0',
          'b': [],
          'c': [],
          'ee': {
              's': '0',
              'd': 'EBiIFxr_o1b4x1YR21PblAFpFG61qDghqFBDyVSOXYW0',
              'br': [],
              'ba': []
          },
          'di': ''}

    ksr0 = KeyStateRecord(**d0)
    d1 = {'vn': [1, 0],
          'i': 'EZ-i0d8JZAoTNZH3ULaU6JR2nmwyvYAfSVPzhzS6b5CM',
          's': '0',
          'p': 'ElsHFkbZQjRb7xHnuE-wyiarIZ9j-1CEQ89I0E3WevcE',
          'd': 'Ey2pXEnaoQVwxA4jB6k0QH5G2Us-0juFL5hOAHAwIEkc',
          'f': '0',
          'dt': '2021-06-09T17:35:54.169967+00:00',
          'et': '2021-06-09T17:35:54.169967+00:00',
          'kt': '1',
          'k': ["DxVTxls3vAwiqmaEXYTs94NUJX6juVSh0xpupEgsAWj_"],
          'nt': '1',
          'n': ["ED6lKZwg-BWl_jlCrjosQkOEhqKD4BJnlqYqWmhqPhaU"],
          'bt': '0',
          'b': [],
          'c': [],
          'ee': {
              's': '0',
              'd': 'EBiIFxr_o1b4x1YR21PblAFpFG61qDghqFBDyVSOXYW0',
              'br': [],
              'ba': []
          },
          'di': ''}
    ksr1 = KeyStateRecord(**d1)

    wat = "BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s"
    diffstate = diffState(wat, ksr0, ksr1)

    # Sequence numbers are the same, digest different == duplicitous
    assert asdict(diffstate) == {'dig': 'Ey2pXEnaoQVwxA4jB6k0QH5G2Us-0juFL5hOAHAwIEkc',
                                 'pre': 'EZ-i0d8JZAoTNZH3ULaU6JR2nmwyvYAfSVPzhzS6b5CM',
                                 'sn': 0,
                                 'state': 'duplicitous',
                                 'wit': 'BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s'}

    # Same state == event
    diffstate = diffState(wat, ksr0, ksr0)
    assert asdict(diffstate) == {'dig': 'EBiIFxr_o1b4x1YR21PblAFpFG61qDghqFBDyVSOXYW0',
                                 'pre': 'EZ-i0d8JZAoTNZH3ULaU6JR2nmwyvYAfSVPzhzS6b5CM',
                                 'sn': 0,
                                 'state': 'even',
                                 'wit': 'BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s'}

    ksr1.s = "2"
    diffstate = diffState(wat, ksr0, ksr1)

    # Sequence numbers are the same, digest different == duplicitous
    assert asdict(diffstate) == {'dig': 'Ey2pXEnaoQVwxA4jB6k0QH5G2Us-0juFL5hOAHAwIEkc',
                                 'pre': 'EZ-i0d8JZAoTNZH3ULaU6JR2nmwyvYAfSVPzhzS6b5CM',
                                 'sn': 2,
                                 'state': 'ahead',
                                 'wit': 'BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s'}

    ksr0.s = "3"
    diffstate = diffState(wat, ksr0, ksr1)

    # Sequence numbers are the same, digest different == duplicitous
    assert asdict(diffstate) == {'dig': 'Ey2pXEnaoQVwxA4jB6k0QH5G2Us-0juFL5hOAHAwIEkc',
                                 'pre': 'EZ-i0d8JZAoTNZH3ULaU6JR2nmwyvYAfSVPzhzS6b5CM',
                                 'sn': 2,
                                 'state': 'behind',
                                 'wit': 'BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s'}


def test_adjudicator():
    default_salt = Salter(raw=b'0123456789abcdef').qb64
    with openHby(name="test", base="test", salt=default_salt, version=KWA["version"]) as hby:
        hab = hby.makeHab("test", **KWA)
        assert hab.pre == "EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3"
        wat = "BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s"
        saider = Saider(qb64b=b'EClqKVJREM3MWKBqR2j712s3Z6rPxhqO-h-p8Ls6_9hQ')

        ksr = hab.kever.state()
        ksr0 = KeyStateRecord(**asdict(ksr))

        hab.db.knas.pin(keys=(hab.pre, wat), val=saider)
        hab.db.ksns.pin(keys=(saider.qb64, ), val=ksr0)
        hab.db.obvs.pin(keys=(hab.pre, wat, hab.pre), val=ObservedRecord(enabled=True))

        adj = Adjudicator(hby=hby, hab=hab)

        adj.adjudicate(hab.pre, 1)
        assert len(adj.cues) == 1
        cue = adj.cues.pull()

        assert cue == {'cid': 'EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3',
                       'kin': 'keyStateConsistent',
                       'oid': 'EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3',
                       'states': [DiffState(pre="EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3",
                                            wit='BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s',
                                            state='even',
                                            sn=0,
                                            dig='EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3')],
                       'wids': {'BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s'}}

        hab.rotate(framed=True, **CUE_KWA)

        adj.adjudicate(hab.pre, 1)
        assert len(adj.cues) == 1
        cue = adj.cues.pull()
        assert cue == {'behind': [DiffState(pre="EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3",
                                            wit='BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s',
                                            state='behind',
                                            sn=0,
                                            dig='EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3')],
                       'cid': 'EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3',
                       'kin': 'keyStateLagging',
                       'oid': 'EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3',
                       'wids': {'BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s'}}

        ksr0.s = '1'
        hab.db.ksns.pin(keys=(saider.qb64, ), val=ksr0)
        adj.adjudicate(hab.pre, 1)
        assert len(adj.cues) == 1
        cue = adj.cues.pull()
        assert cue == {'cid': 'EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3',
                       'dups': [DiffState(pre="EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3",
                                          wit='BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s',
                                          state='duplicitous',
                                          sn=1,
                                          dig='EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3')],
                       'kin': 'keyStateDuplicitous',
                       'oid': 'EIaGMMWJFPmtXznY1IIiKDIrg-vIyge6mBl2QV8dDjI3',
                       'wids': {'BbIg_3-11d3PYxSInLN-Q9_T2axD6kkXd3XRgbGZTm6s'}}

        with pytest.raises(ValueError):
            adj.adjudicate(hab.pre, 2)
