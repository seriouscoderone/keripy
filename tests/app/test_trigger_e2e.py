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


def _mandate(jurisdiction="US-UT", coverages=("BI", "PD")):
    """A mandate as a real SAD: `d` is its own SAID, exactly like an ACDC."""
    sad = dict(d="", kind="mandate", line_of_business="general_liability",
               jurisdiction=jurisdiction, coverages=list(coverages))
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
        pro = ProdClient(hab=act).request(pre=cuo.pre, said=said)  # default route
        responder = ProdResponder(hab=cuo, kvy=kvyA, disclosable={said: mandate},
                                  policy=allowList(act.pre))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(ims=bytearray(pro), kvy=kvyA)

        barMsg = responder.service()          # a bytearray -- never list() this
        assert barMsg, "the responder produced no bar"

        bar = serdering.SerderKERI(raw=bytes(barMsg))
        got = ProdClient(hab=act).harvest(serder=bar, said=said)
        assert got == mandate
        assert verifySealedBody(seal={"d": said}, body=got) is True


def test_a_dishonest_responder_cannot_substitute_a_body():
    """The actual threat model: the sender is untrusted, so the sender lies.

    Nothing in prod/bare makes a responder disclose the body it anchored.
    `ProdResponder` will sign and send whatever its controller put in
    `disclosable` under the requested SAID, and `bare()` does not enforce the
    keying either -- so a compromised or malicious A can answer a prod for S
    with a completely different mandate, and even set that mandate's own `d`
    to S so the lie is self-consistent.

    The substitution happens ON THE WIRE, in the responder's configuration:
    the bar A signs and B parses really does carry the wrong body. B never
    compares it against a local copy -- it has none, that is the whole point of
    log-triggered retrieval -- it re-derives against the digest it read from
    A's KEL, and that is what catches this.

    The honest control comes first, through the same path, so the False below
    is attributable to the substitution and not to a broken transport.
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

        # Control: an HONEST responder, same request, same delivery.
        honest = ProdResponder(hab=cuo, kvy=kvyA, disclosable={said: mandate},
                               policy=allowList(act.pre))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(ProdClient(hab=act).request(pre=cuo.pre, said=said)),
            kvy=kvyA)
        barMsg = honest.service()
        assert barMsg, "control failed: the honest responder produced no bar"
        got = ProdClient(hab=act).harvest(
            serder=serdering.SerderKERI(raw=bytes(barMsg)), said=said)
        assert verifySealedBody(seal={"d": said}, body=got) is True

        # The lie: a DIFFERENT mandate, wearing the requested SAID as its own `d`
        # so that a consumer inspecting the body sees exactly what it asked for.
        substitute, otherSaid = _mandate(jurisdiction="US-NV",
                                         coverages=["BI", "PD", "UM"])
        assert otherSaid != said
        substitute = dict(substitute, d=said)

        liar = ProdResponder(hab=cuo, kvy=kvyA, disclosable={said: substitute},
                             policy=allowList(act.pre))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(ProdClient(hab=act).request(pre=cuo.pre, said=said)),
            kvy=kvyA)
        badMsg = liar.service()
        assert badMsg, "the dishonest responder sent nothing -- nothing was tested"

        badBar = serdering.SerderKERI(raw=bytes(badMsg))
        # The substitution really is on the wire: it is in the signed bytes A
        # produced, filed under the SAID B asked for, and it is not a local edit.
        assert badBar.ked["a"][said] == substitute
        assert substitute["jurisdiction"].encode() in badBar.raw
        assert mandate["jurisdiction"].encode() not in badBar.raw

        lied = ProdClient(hab=act).harvest(serder=badBar, said=said)
        assert lied != mandate
        assert lied["d"] == said              # the lie is self-consistent
        assert verifySealedBody(seal={"d": said}, body=lied) is False


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
            ims=bytearray(ProdClient(hab=act).request(pre=cuo.pre, said=said)),
            kvy=kvyA)
        assert admitted.service(), "control failed: an ADMITTED pro produced no bar either"

        # Same request, same everything, only the policy changes.
        refused = ProdResponder(hab=cuo, kvy=kvyA, disclosable={said: mandate},
                                policy=allowList("ESomeoneElse"))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(ProdClient(hab=act).request(pre=cuo.pre, said=said)),
            kvy=kvyA)
        assert refused.service() == bytearray(), "policy did not withhold"
