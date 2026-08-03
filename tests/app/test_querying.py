# -*- encoding: utf-8 -*-
"""
keri.app.querying module

"""
from hio.base import doing

from keri.kering import Vrsn_1_0, Kinds
from keri.app import (QueryDoer, KeyStateNoticer, LogQuerier,
                      SeqNoQuerier, AnchorQuerier, openHby)

from keri.core import SerderKERI, Parser, reply
from keri.db import dgKey

from tests.common import CUE_KWA, KWA


def test_querying():
    with openHby(version=Vrsn_1_0) as hby, \
            openHby(version=Vrsn_1_0) as hby1:
        inqHab = hby.makeHab(name="inquisitor", **KWA)
        subHab = hby1.makeHab(name="subject", **KWA)
        qdoer = QueryDoer(hby=hby, hab=inqHab, kvy=hby.kvy, pre=subHab.pre)

        icp = subHab.msgOwnInception(framed=True, gvrsn=Vrsn_1_0)
        Parser(version=Vrsn_1_0).parseOne(ims=bytearray(icp), kvy=inqHab.kvy)

        assert qdoer is not None

        tock = 0.03125
        limit = 1.0
        doist = doing.Doist(limit=limit, tock=tock, real=True)

        # doist.do(doers=doers)
        deeds = doist.enter(doers=[qdoer])

        assert len(qdoer.doers) == 1
        ksnDoer = qdoer.doers[0]
        assert isinstance(ksnDoer, KeyStateNoticer)
        assert len(ksnDoer.witq.msgs) == 1
        msg = ksnDoer.witq.msgs.popleft()
        assert msg["src"] == inqHab.pre
        assert msg["pre"] == subHab.pre
        assert msg["r"] == "ksn"
        assert msg["q"] == {'fn': '0', 's': '0'}
        assert msg["wits"] is None

        doist.recur(deeds=deeds)

        # Cue up a saved key state equal to the one we have
        hby.kvy.cues.clear()
        ksr = subHab.kever.state()
        rpy = reply(route="/ksn", data=ksr._asdict(), **KWA)
        cue = dict(kin="keyStateSaved", ksn=ksr._asdict())
        hby.kvy.cues.append(cue)

        doist.recur(deeds=deeds)

        # We already have up to date key state so loaded will be true
        assert qdoer.done is True
        assert len(hby.kvy.cues) == 0

        # create a new query doer
        qdoer = QueryDoer(hby=hby, hab=inqHab, kvy=hby.kvy, pre=subHab.pre)
        tock = 0.03125
        limit = 1.0
        doist = doing.Doist(limit=limit, tock=tock, real=True)

        # rotate AID and submit as a new keyStateSave
        rot = subHab.rotate(framed=True, **CUE_KWA)
        ksr = subHab.kever.state()
        rpy = reply(route="/ksn", data=ksr._asdict(), **KWA)
        cue = dict(kin="keyStateSaved", ksn=ksr._asdict())
        hby.kvy.cues.append(cue)
        deeds = doist.enter(doers=[qdoer])
        doist.recur(deeds=deeds)

        # We are behind in key state, so we aren't done and have queried for the logs
        assert qdoer.done is False
        assert len(qdoer.doers) == 1
        ksnDoer = qdoer.doers[0]
        assert isinstance(ksnDoer, KeyStateNoticer)
        assert len(ksnDoer.witq.msgs) == 1

        assert len(ksnDoer.doers) == 1
        logDoer = ksnDoer.doers[0]
        assert isinstance(logDoer, LogQuerier)
        assert len(hby.kvy.cues) == 0

        Parser(version=Vrsn_1_0).parseOne(ims=bytearray(rot), kvy=inqHab.kvy)
        doist.recur(deeds=deeds)

        assert qdoer.done is True

        # Test sequence querier
        sdoer = SeqNoQuerier(hby=hby, hab=inqHab, pre=subHab.pre, sn=5)
        assert len(sdoer.witq.msgs) == 1

        tock = 0.03125
        limit = 1.0
        doist = doing.Doist(limit=limit, tock=tock, real=True)
        deeds = doist.enter(doers=[sdoer])
        doist.recur(deeds=deeds)
        assert len(sdoer.witq.msgs) == 0

        sdoer = SeqNoQuerier(hby=hby, hab=inqHab, pre=subHab.pre, sn=1)
        assert len(sdoer.witq.msgs) == 1

        tock = 0.03125
        limit = 1.0
        doist = doing.Doist(limit=limit, tock=tock, real=True)
        deeds = doist.enter(doers=[sdoer])
        doist.recur(deeds=deeds)
        assert len(sdoer.witq.msgs) == 1

        sdoer = SeqNoQuerier(hby=hby, hab=inqHab, pre=subHab.pre, fn=2, sn=4)
        assert len(sdoer.witq.msgs) == 1
        msg = sdoer.witq.msgs.pull()
        query = msg['q']
        assert query == {'fn': '2', 's': '4'}

        # Test with originally unknown AID
        sdoer = SeqNoQuerier(hby=hby, hab=inqHab, pre="ExxCHAI9bkl50F5SCKl2AWQbFGKeJtz0uxM2diTMxMQA", sn=1)
        assert len(sdoer.witq.msgs) == 1

        tock = 0.03125
        limit = 1.0
        doist = doing.Doist(limit=limit, tock=tock, real=True)
        deeds = doist.enter(doers=[sdoer])
        doist.recur(deeds=deeds)
        assert len(sdoer.witq.msgs) == 1

        # Test anchor querier
        adoer = AnchorQuerier(hby=hby, hab=inqHab, pre=subHab.pre, anchor={'s': '5'})
        assert len(adoer.witq.msgs) == 1

        tock = 0.03125
        limit = 1.0
        doist = doing.Doist(limit=limit, tock=tock, real=True)
        deeds = doist.enter(doers=[adoer])
        doist.recur(deeds=deeds)
        assert len(sdoer.witq.msgs) == 1

        # Test with originally unknown AID
        adoer = AnchorQuerier(hby=hby, hab=inqHab, pre="ExxCHAI9bkl50F5SCKl2AWQbFGKeJtz0uxM2diTMxMQA",
                              anchor={'s': '5'})
        assert len(adoer.witq.msgs) == 1

        tock = 0.03125
        limit = 1.0
        doist = doing.Doist(limit=limit, tock=tock, real=True)
        deeds = doist.enter(doers=[adoer])
        doist.recur(deeds=deeds)
        assert len(adoer.witq.msgs) == 1

def test_query_not_found_escrow():
    with openHby(version=Vrsn_1_0) as hby, \
            openHby(version=Vrsn_1_0) as hby1:
        inqHab = hby.makeHab(name="inquisitor", **KWA)
        subHab = hby1.makeHab(name="subject", **KWA)

        icp = inqHab.msgOwnInception(framed=True, gvrsn=Vrsn_1_0)
        subHab.psr.parseOne(ims=icp)
        assert inqHab.pre in subHab.kevers

        qry = inqHab.query(subHab.pre, route="/foo", src=inqHab.pre, **CUE_KWA)
        serder = SerderKERI(raw=qry)
        dgkey = dgKey(inqHab.pre, serder.saidb)

        subHab.db.evts.put(keys=(inqHab.pre, serder.saidb), val=serder)
        subHab.db.qnfs.add(keys=(inqHab.pre, serder.said), val=serder.saidb)

        subHab.kvy.processQueryNotFound()
        assert subHab.db.qnfs.get(dgkey) == []


def test_anchor_querier_terminates_on_seal_types():
    """AnchorQuerier must COMPLETE once its anchor lands in the watched KEL.

    The pre-existing coverage in test_querying only asserts that a query was
    QUEUED (len(witq.msgs) == 1); it never asserts the doer finishes, so the
    completion predicate was untested. It used fetchLastSealingEventByEventSeal,
    which hard-returns None for any seal that is not a SealEvent(i,s,d) -- so a
    SealDigest(d) anchor could never terminate the doer and it polled forever.

    Runs at keripy's default protocol version rather than Vrsn_1_0: ingesting a
    peer's ixn events via replay only lands under the default here.
    """
    from keri.core import eventing

    with openHby() as hby, openHby() as hby1:
        inqHab = hby.makeHab(name="inquisitor")
        subHab = hby1.makeHab(name="subject")

        # subject anchors BOTH a bare digest seal and a full event seal
        dig = "EBqeNn23Hnt5y5cJzpfaXm-1kYylZEdY1AQCuWiQs44J"
        digest_seal = dict(d=dig)
        event_seal = dict(i=subHab.pre, s="0", d=subHab.kever.serder.said)
        subHab.interact(data=[digest_seal])
        subHab.interact(data=[event_seal])

        # inquisitor ingests the subject's KEL as a remote peer would
        kvy = eventing.Kevery(db=hby.db, lax=True, local=False)
        Parser(kvy=kvy).parse(ims=bytearray(subHab.replay()), kvy=kvy)
        assert subHab.pre in kvy.kevers
        assert kvy.kevers[subHab.pre].sn == 2

        # Assert on the predicate's own effect -- it removes the querier doer
        # when the anchor is found. Asserting only on recur()'s return value
        # would be vacuous: DoDoer.recur() with no deeds returns True anyway.
        def fired(anchor):
            adoer = AnchorQuerier(hby=hby, hab=inqHab, pre=subHab.pre,
                                  anchor=anchor)
            assert adoer.witq in adoer.doers  # armed before recur
            done = adoer.recur(tyme=0.0)
            return done is True and adoer.witq not in adoer.doers

        # a SealDigest anchor must terminate the doer -- this is the regression
        assert fired(digest_seal)

        # a SealEvent anchor must still terminate it (no behaviour lost)
        assert fired(event_seal)

        # an anchor that never landed must NOT terminate it
        absent = "EAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        assert not fired(dict(d=absent))
        assert not fired(dict(i=subHab.pre, s="0", d=absent))

    """Done Test"""
