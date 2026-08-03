# -*- encoding: utf-8 -*-
"""Plan A acceptance: a watching party detects and verifies a peer's new fact.

Direct mode, two Habs in one process, bytes moved by calling Parser.parse()
directly -- no mailbox, no network. Every step drives the entry point a real
caller uses; nothing here calls a handler by hand.
"""
from keri.app import habbing
from keri.app.anchoring import AnchorWatcher
from keri.app.prodding import ProdClient, ProdResponder, allowList
from keri.core import coring, eventing, parsing, serdering
from keri.core.sealing import verifySealedBody
from keri.kering import Vrsn_1_0


def _mandate():
    """A mandate as a real SAD: `d` is its own SAID, exactly like an ACDC."""
    sad = dict(d="", kind="mandate", line_of_business="general_liability",
               jurisdiction="US-UT", coverages=["BI", "PD"])
    _, saidified = coring.Saider.saidify(sad=dict(sad))
    return saidified, saidified["d"]


def test_watch_then_prod_then_verify():
    """The whole trigger. B is told nothing; it finds the fact by watching."""
    with habbing.openHby(name="cuo", temp=True) as hbyA, \
            habbing.openHby(name="act", temp=True) as hbyB:
        cuo = hbyA.makeHab(name="cuo")
        act = hbyB.makeHab(name="actuary")
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)

        # OOBI stand-in, both directions. UNPINNED parser: Habery emits a v2 KEL
        # and a v1-pinned parser drops it silently, leaving the Kevery empty.
        parsing.Parser(kvy=kvyA).parse(ims=bytearray(act.replay()), kvy=kvyA)

        # A anchors a mandate. Nobody is told.
        mandate, said = _mandate()
        cuo.interact(data=[dict(d=said)])
        parsing.Parser(kvy=kvyB).parse(ims=bytearray(cuo.replay()), kvy=kvyB)

        # B watches A's log and finds it, having been told nothing.
        watcher = AnchorWatcher(hab=act, pre=cuo.pre)
        anchors = watcher.since(sn=0)
        assert [s["d"] for _, s in anchors] == [said]

        # B prods. A answers under an allowlist that admits B.
        pro = ProdClient(hab=act).request(pre=cuo.pre, said=said, route="sealed")
        responder = ProdResponder(hab=cuo, kvy=kvyA, disclosable={said: mandate},
                                  policy=allowList(act.pre))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(ims=bytearray(pro), kvy=kvyA)

        barMsg = responder.service()          # a bytearray -- never list() this
        assert barMsg, "the responder produced no bar"

        bar = serdering.SerderKERI(raw=bytes(barMsg))
        got = ProdClient(hab=act).harvest(serder=bar, said=said)
        assert got == mandate
        assert verifySealedBody(seal={"d": said}, body=got) is True


def test_a_tampered_bar_is_rejected_without_trusting_the_sender():
    """A REAL bar, tampered in flight, is rejected -- the sender is never trusted.

    Built through the responder rather than hand-rolled, so this exercises the
    delivery path the honest case uses and differs from it only in the tamper.
    """
    with habbing.openHby(name="cuo2", temp=True) as hbyA, \
            habbing.openHby(name="act2", temp=True) as hbyB:
        cuo = hbyA.makeHab(name="cuo")
        act = hbyB.makeHab(name="actuary")
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        parsing.Parser(kvy=kvyA).parse(ims=bytearray(act.replay()), kvy=kvyA)

        mandate, said = _mandate()
        cuo.interact(data=[dict(d=said)])
        parsing.Parser(kvy=kvyB).parse(ims=bytearray(cuo.replay()), kvy=kvyB)

        pro = ProdClient(hab=act).request(pre=cuo.pre, said=said, route="sealed")
        responder = ProdResponder(hab=cuo, kvy=kvyA, disclosable={said: mandate},
                                  policy=allowList(act.pre))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(ims=bytearray(pro), kvy=kvyA)
        barMsg = responder.service()
        assert barMsg

        bar = serdering.SerderKERI(raw=bytes(barMsg))
        got = ProdClient(hab=act).harvest(serder=bar, said=said)
        assert verifySealedBody(seal={"d": said}, body=got) is True   # honest baseline

        # The tamper: the jurisdiction B actually cares about, changed in flight.
        tampered = dict(got, jurisdiction="US-NV")
        assert verifySealedBody(seal={"d": said}, body=tampered) is False


def test_an_unauthorized_watcher_gets_nothing():
    """Watching a log is not authority to read what it commits to.

    B sees the anchor -- anchors are public -- but the policy does not admit B,
    so no bar comes back. Anchoring is not consent.

    The withholding assertion is an ASSERTION ON AN ABSENCE, which is satisfied
    by any failure that produces no bar -- a version skew, an unparsed KEL, a
    dropped cue. So this first proves a bar DOES come back for the same request
    under a policy that admits B, then changes only the policy. The contrast is
    what makes the absence attributable to authorization rather than to
    something merely being broken.
    """
    with habbing.openHby(name="cuo3", temp=True) as hbyA, \
            habbing.openHby(name="act3", temp=True) as hbyB:
        cuo = hbyA.makeHab(name="cuo")
        act = hbyB.makeHab(name="actuary")
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        parsing.Parser(kvy=kvyA).parse(ims=bytearray(act.replay()), kvy=kvyA)

        mandate, said = _mandate()
        cuo.interact(data=[dict(d=said)])
        parsing.Parser(kvy=kvyB).parse(ims=bytearray(cuo.replay()), kvy=kvyB)

        assert [s["d"] for _, s in AnchorWatcher(hab=act, pre=cuo.pre).since(sn=0)] == [said]

        # Control: the identical request under a policy that DOES admit B.
        # Without this, the withholding assertion below cannot tell "denied"
        # from "the pro never arrived".
        admitted = ProdResponder(hab=cuo, kvy=kvyA, disclosable={said: mandate},
                                 policy=allowList(act.pre))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(ProdClient(hab=act).request(pre=cuo.pre, said=said, route="sealed")),
            kvy=kvyA)
        assert admitted.service(), "control failed: an ADMITTED pro produced no bar either"

        # Same request, same everything, only the policy changes.
        refused = ProdResponder(hab=cuo, kvy=kvyA, disclosable={said: mandate},
                                policy=allowList("ESomeoneElse"))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(ProdClient(hab=act).request(pre=cuo.pre, said=said, route="sealed")),
            kvy=kvyA)
        assert refused.service() == bytearray(), "policy did not withhold"
