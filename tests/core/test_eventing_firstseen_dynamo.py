# -*- encoding: utf-8 -*-
"""
tests.core.test_eventing_firstseen_dynamo module

End-to-end routing tests for the KERI-layer first-seen gate (Kever._claimFirstSeen
/ _supersedeFirstSeen + the logEvent gate) over a moto-backed DynamoDBer whose
singleWriter is False, so the gate -- not the in-memory db.kels.getLast duplicity
check -- catches a concurrent conflict at the same (pre, sn) slot.

The realistic deployment is N witness Lambda instances = N db handles (each with
its own in-memory .kevers cache) over ONE DynamoDB table; the fseen. marker is the
cross-instance coordination. The concurrent / recovery tests model that by opening
a SECOND DynamoDBer handle on the same moto table whose kever is still at the prior
sn, so the conflicting event reaches logEvent (the accept path) rather than being
short-circuited by the shared in-memory in-order / getLast check.
"""
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri import Kinds
from keri.app import habbing
from keri.core import eventing
from keri.core.eventing import incept, rotate, interact, Kever, Kevery
from keri.core import Salter, Diger
from keri.db.dbing import snKey
from keri.kering import LikelyDuplicitousError


@pytest.fixture
def dynamo_hby():
    """A Habery whose db is a moto-backed DynamoDBer (singleWriter False -> gate active).

    The DynamoDBer is wired with lambding.setup_baser (the production witness-Lambda
    path) so it carries the full Baser attribute surface (evts/fels/kels/kevers/...)
    that Habery expects -- a bare DynamoDBer has no .kevers. The keystore stays a
    temp LMDB Manager (temp=True) so concurrent test runs don't collide on it.
    """
    if not HAS_MOTO:
        pytest.skip("requires moto")
    from keri.db.dynamodbing import DynamoDBer
    from keri.app.lambding import BASER_STORES, setup_baser
    with mock_aws():
        db = DynamoDBer.open(name="wit", stores=BASER_STORES, region="us-east-1")
        assert db.singleWriter is False
        setup_baser(db)
        hby = habbing.Habery(name="wit", temp=True, free=True, db=db)
        yield hby
        hby.close()
        db.close(clear=True)


def _marker(db, pre, sn):
    return db.getVal(db.env.open_db(b"fseen."), snKey(pre, sn))


def _make_ixn(hab, sn, data):
    """Clone of habbing.Hab.interact's event construction (habbing.py:1470):
    build an ixn serder anchored to the hab's CURRENT accepted event (its icp at
    sn=0) at the given sn, then sign with the hab's controller keys. Returns
    (serder, sigers) WITHOUT advancing the hab's own kever -- the conflicting
    events are fed through a separate Kevery instead.
    """
    kever = hab.kever
    serder = interact(pre=kever.prefixer.qb64,
                      dig=kever.serder.said,  # prior == the icp at sn=0
                      sn=sn,
                      data=data,
                      kind=Kinds.json)  # match makeHab's v1 JSON KEL
    sigers = hab.sign(ser=serder.raw)
    return serder, sigers


def _process(kvy, serder, sigers):
    """Feed one already-constructed event + its controller sigers through a Kevery,
    exactly as Hab.interact/rotate do (habbing.py:1483, 1426)."""
    kvy.processEvent(serder=serder, sigers=sigers)


def _second_handle():
    """Open a SECOND bare DynamoDBer handle on the SAME moto table, modelling a
    second witness Lambda instance: independent in-memory .kevers cache, shared
    backing table. setup_baser attaches the Baser surface; reload_baser rebuilds
    the icp kever from the table's stored key state, so a same-sn conflicting event
    reaches the first-seen gate in logEvent (its kever is still at the prior sn,
    so the in-order / getLast checks do not short-circuit it first).

    No second Manager/keystore is needed -- the conflicting events are already
    signed; this handle only verifies + processes them through a Kevery. This is
    exactly the lambding production wiring (setup_baser + reload_baser)."""
    from keri.db.dynamodbing import DynamoDBer
    from keri.app.lambding import BASER_STORES, setup_baser, reload_baser
    db2 = DynamoDBer.open(name="wit", stores=BASER_STORES, region="us-east-1")
    setup_baser(db2)
    reload_baser(db2)  # rebuild kevers from the shared table's stored key state
    return db2


def test_firstseen_win_marks_slot(dynamo_hby):
    """An accepted first event claims the (pre, sn) marker."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    assert _marker(dynamo_hby.db, hab.pre.encode(), 0) == hab.kever.serder.saidb


def test_concurrent_different_said_is_duplicity(dynamo_hby):
    """Two different events at the same sn: first wins, second raises
    LikelyDuplicitousError via the gate."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    serderA, sigersA = _make_ixn(hab, sn=1, data=[{"d": "A"}])
    serderB, sigersB = _make_ixn(hab, sn=1, data=[{"d": "B"}])

    # Open the second instance BEFORE either sn=1 event is processed, so its
    # reloaded kever is at the shared sn=0 state (icp only) -- exactly the race
    # the gate guards: two Lambdas each holding an sn=0 kever both write sn=1.
    db2 = _second_handle()
    try:
        kvy2 = eventing.Kevery(db=db2, lax=False, local=True)

        # First instance accepts A: advances its kever to sn=1, claims the marker.
        kvy = eventing.Kevery(db=dynamo_hby.db, lax=False, local=True)
        _process(kvy, serderA, sigersA)              # A wins
        assert _marker(dynamo_hby.db, hab.pre.encode(), 1) == serderA.saidb

        # Second instance (its own kever still at sn=0) processes B at sn=1: passes
        # its in-order check, reaches logEvent, and the first-seen gate -- not the
        # in-memory getLast check -- catches the conflict.
        with pytest.raises(LikelyDuplicitousError):
            _process(kvy2, serderB, sigersB)         # B is duplicity (caught by the gate)
    finally:
        db2.close()

    assert _marker(dynamo_hby.db, hab.pre.encode(), 1) == serderA.saidb
    # The loser is escrowed as evidence in ldes (mirrors detected duplicity).
    escrowed = [edig.encode() if isinstance(edig, str) else bytes(edig)
                for (_pre,), sn, edig in
                dynamo_hby.db.ldes.getAllItemIter(keys=hab.pre.encode()) if sn == 1]
    assert serderB.saidb in escrowed


def test_same_said_redelivery_idempotent(dynamo_hby):
    """Re-delivering the exact same event assigns no second fn."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    serderA, sigersA = _make_ixn(hab, sn=1, data=[{"d": "A"}])

    # Open the second instance while state is still sn=0, so its kever holds sn=0
    # and re-delivering A reaches logEvent (rather than being short-circuited).
    db2 = _second_handle()
    try:
        kvy2 = eventing.Kevery(db=db2, lax=False, local=True)

        kvy = eventing.Kevery(db=dynamo_hby.db, lax=False, local=True)
        _process(kvy, serderA, sigersA)

        # Re-deliver the identical event through the second instance: it reaches
        # logEvent, the gate's putVal loses to A's marker, but the incumbent said
        # == this said -> idempotent (first=False), no new fn assigned.
        _process(kvy2, serderA, sigersA)
    finally:
        db2.close()

    # The slot still holds the original said and no second first-seen was logged.
    assert _marker(dynamo_hby.db, hab.pre.encode(), 1) == serderA.saidb
    fels = list(dynamo_hby.db.fels.getAllItemIter(keys=hab.pre.encode()))
    assert len(fels) == 2  # icp (fn=0) + the single ixn (fn=1); no dup


def test_recovery_supersedes_marker(dynamo_hby):
    """A validated superseding rot at the same sn overwrites the marker.

    Self-contained raw-signer build (cloned from tests/core/test_eventing_v1.py
    test_recovery): icp@0, ixn@1, then a recovery rot@1 (dig -> icp, signed by the
    pre-committed next key) processed via Kever.update. Because the rot sits at the
    current accepted sn (1 <= 1), Kever.update passes supersede=True to logEvent,
    which overwrites the fseen. marker with the rot's said.
    """
    db = dynamo_hby.db
    salt = b'g\x15\x89\x1a@\xa4\xa47\x07\xb9Q\xb8\x18\xcdJW'
    signers = Salter(raw=salt).signers(count=4, transferable=True)

    # Event 0: inception (current key signers[0], next-key digest of signers[1]).
    icp = incept(keys=[signers[0].verfer.qb64],
                 ndigs=[Diger(ser=signers[1].verfer.qb64b).qb64],
                 kind=Kinds.json)
    sig0 = signers[0].sign(icp.raw, index=0)
    kever = Kever(serder=icp, sigers=[sig0], db=db, local=True)
    pre = kever.prefixer.qb64
    assert _marker(db, pre.encode(), 0) == icp.saidb

    # Event 1 (ixn): interaction anchored to the icp, signed by the current key.
    ixn = interact(pre=pre, dig=kever.serder.said, sn=1, data=[{"d": "ixn-at-1"}],
                   kind=Kinds.json)
    sig1 = signers[0].sign(ixn.raw, index=0)
    kever.update(serder=ixn, sigers=[sig1], local=True)
    assert kever.sner.num == 1
    assert _marker(db, pre.encode(), 1) == ixn.saidb

    # Event 1' (rot recovery): a rotation at the SAME sn that supersedes the ixn.
    # dig points back to the icp (sn=0); current keys are signers[1] (the revealed
    # pre-committed next key); next-key digest commits signers[2].
    rot = rotate(pre=pre,
                 keys=[signers[1].verfer.qb64],
                 dig=icp.said,
                 ndigs=[Diger(ser=signers[2].verfer.qb64b).qb64],
                 sn=1,
                 kind=Kinds.json)
    rsig = signers[1].sign(rot.raw, index=0)
    kever.update(serder=rot, sigers=[rsig], local=True)

    assert kever.sner.num == 1
    assert kever.serder.said == rot.said
    assert _marker(db, pre.encode(), 1) == rot.saidb


def test_escrowLDEvent_writes_to_ldes(dynamo_hby):
    """escrowLDEvent must land the event in the ldes store (regression for the
    pre-existing addLde partial-migration bug)."""
    hab = dynamo_hby.makeHab(name="ctrl", icount=1, ncount=1, transferable=True)
    serderB, sigersB = _make_ixn(hab, sn=1, data=[{"d": "B"}])
    kvy = eventing.Kevery(db=dynamo_hby.db, lax=False, local=True)
    kvy.escrowLDEvent(serder=serderB, sigers=sigersB)
    escrowed = [edig.encode() if isinstance(edig, str) else bytes(edig)
                for (_pre,), _sn, edig in
                dynamo_hby.db.ldes.getAllItemIter(keys=hab.pre.encode())]
    assert serderB.saidb in escrowed
