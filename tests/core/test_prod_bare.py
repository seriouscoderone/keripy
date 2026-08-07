# -*- encoding: utf-8 -*-
"""
tests.core.test_prod_bare module

Round-trip tests for the Prod ('pro') / Bare ('bar') content-retrieval pair.

`pro` requests disclosure of data committed to by a seal in a KEL; `bar`
discloses it. Authority comes from the anchor, not from the sender: the
returned body must re-derive to the SAID the seal committed to.

Every test here drives the **real entry point** — bytes into `Parser.parse()`
with a live `Kevery` — rather than calling the handlers directly. A `pro` that
only works when the handler is invoked by hand is not a working `pro`.
"""

import pytest

from keri.app import habbing
from keri.app.prodding import (ProdResponder, ProdResponderDoer,
                               allowList, openPolicy)
from keri.core import coring, eventing, parsing, serdering
from keri.core.eventing import bare, prod
from keri.kering import Kinds, ValidationError, Vrsn_1_0


def anchorSad(hab, sad):
    """Saidify `sad`, anchor its SAID in `hab`'s KEL via an ixn, return the SAID."""
    _, saidified = coring.Saider.saidify(sad=dict(sad))
    said = saidified["d"]
    hab.interact(data=[dict(d=said)])
    return said, saidified


def giveKel(srcHab, dstKvy):
    """Replay srcHab's KEL into dstKvy the way an OOBI/witness replay would.

    Deliberately does NOT pin the parser to Vrsn_1_0. Habery emits a v2 KEL by
    default, and parsing that with a v1 parser fails *silently* -- it logs a
    genus-skew warning, leaves the Kevery empty, and raises nothing. The
    callers below assert the seal actually landed, so this stays honest.
    """
    parsing.Parser(kvy=dstKvy).parse(ims=bytearray(srcHab.replay()), kvy=dstKvy)


def sealedSaids(db, pre):
    """Every SAID anchored by a digest seal in pre's KEL, in order."""
    saids = []
    for evt in db.getEvtPreIter(pre=pre.encode()):
        serder = evt if hasattr(evt, "seals") else serdering.SerderKERI(raw=bytes(evt))
        for seal in (serder.seals or []):
            if "d" in seal and len(seal) == 1:
                saids.append(seal["d"])
    return saids


def buildPro(hab, said, route="sealed"):
    """A signed `pro` asking for the SAD committed to by `said`."""
    serder = prod(pre=hab.pre, route=route, query=dict(d=said),
                  pvrsn=Vrsn_1_0, kind=Kinds.json)
    return hab.endorse(serder=serder, last=False, framed=True, gvrsn=Vrsn_1_0)


def test_pro_reaches_kevery_through_the_parser():
    """The seam itself: a wire `pro` must actually arrive at Kevery.processPro.

    Regression for the defect this work fixes — `Parser.msgProcess` had no
    `pro`/`bar` branch, so a signed `pro` fell through to
    `ValidationError: Unexpected message ilk='pro'`, which `parse()` swallows
    at DEBUG. The stub was unreachable from the wire; only a direct
    `processMsg()` call (which nothing in src/ makes) could reach it.
    """
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, _ = anchorSad(habA, dict(d="", mandate="auto/UT"))

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)  # A must know B's key state to authenticate the pro

        seen = []
        orig = eventing.Kevery.processPro
        eventing.Kevery.processPro = (
            lambda self, serder, **kwa: (seen.append(serder.said),
                                         orig(self, serder, **kwa))[1])
        try:
            parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
                ims=bytearray(buildPro(habB, said)), kvy=kvyA)
        finally:
            eventing.Kevery.processPro = orig

        assert len(seen) == 1, "wire `pro` never reached Kevery.processPro"


def test_round_trip_pro_to_bar_through_kevery():
    """Full round trip through the real entry point.

    B watches A's log, sees an anchor, `pro`s for the body, and A answers with
    a `bar` whose body re-derives to the SAID the seal committed to.
    """
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        sad = dict(d="", line_of_business="auto", jurisdiction="UT",
                   coverages=["BI", "PD"])
        said, saidified = anchorSad(habA, sad)

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        giveKel(habB, kvyA)
        giveKel(habA, kvyB)  # B has A's KEL, so B can read the seal

        # B reads the seal off A's log — this is where the SAID comes from
        assert said in sealedSaids(hbyB.db, habA.pre)

        # A consents to disclose this SAD, to anyone. Anchoring alone is NOT consent.
        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified},
                                  policy=openPolicy)

        # --- pro over the wire into A ---
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)

        barMsg = responder.service()
        assert barMsg, "A produced no `bar` for an authorized `pro`"

        # --- bar over the wire into B ---
        parsing.Parser(kvy=kvyB, version=Vrsn_1_0).parse(
            ims=bytearray(barMsg), kvy=kvyB)

        cues = [c for c in kvyB.cues if c["kin"] == "bare"]
        assert len(cues) == 1
        body = cues[0]["sad"]

        # the load-bearing check: the body re-derives to the SEALED said
        rederived, _ = coring.Saider.saidify(sad=dict(body))
        assert rederived.qb64 == said
        assert body["line_of_business"] == "auto"


def test_correlation_key_is_discloser_plus_said():
    """Correlation between a prod and its bare is by (discloser AID, SAID).

    `bar` carries no reference to the `pro` that asked -- no prior field, no
    echo of the prod's own `d` -- so the SAID its `a` block is keyed by is the
    only handle, and `source` disambiguates asking two parties for the same
    SAID. Both disclosers here anchor the SAME SAD, so the SAID alone is
    ambiguous and only the pair identifies who answered.
    """
    with habbing.openHby(name="d1", temp=True) as hby1, \
            habbing.openHby(name="d2", temp=True) as hby2, \
            habbing.openHby(name="req", temp=True) as hbyB:
        hab1 = hby1.makeHab(name="discloser1")
        hab2 = hby2.makeHab(name="discloser2")
        hbyB.makeHab(name="requester")

        sad = dict(d="", mandate="auto/UT")
        said1, sadified = anchorSad(hab1, sad)
        said2, _ = anchorSad(hab2, sad)
        assert said1 == said2, "same content must yield the same SAID"

        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        giveKel(hab1, kvyB)
        giveKel(hab2, kvyB)

        for hab in (hab1, hab2):
            serder = bare(pre=hab.pre, route="sealed", data={said1: sadified},
                          pvrsn=Vrsn_1_0, kind=Kinds.json)
            parsing.Parser(kvy=kvyB, version=Vrsn_1_0).parse(
                ims=bytearray(hab.endorse(serder=serder, last=False,
                                          framed=True, gvrsn=Vrsn_1_0)),
                kvy=kvyB)

        cues = [c for c in kvyB.cues if c["kin"] == "bare"]
        assert len(cues) == 2
        assert {c["said"] for c in cues} == {said1}, "SAID alone is ambiguous"
        assert {c["source"] for c in cues} == {hab1.pre, hab2.pre}, \
            "source must distinguish who disclosed"


def test_bar_for_a_different_said_does_not_answer_the_prod():
    """A bare keyed by some other anchored SAID is a valid disclosure of that
    other thing -- it just is not an answer to this prod. Correlation is by
    SAID, so the requester matches on the key, not on arrival order."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        hbyB.makeHab(name="requester")

        wanted, _ = anchorSad(habA, dict(d="", mandate="auto/UT"))
        other, otherSad = anchorSad(habA, dict(d="", mandate="home/CA"))
        assert wanted != other

        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        giveKel(habA, kvyB)

        serder = bare(pre=habA.pre, route="sealed", data={other: otherSad},
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        parsing.Parser(kvy=kvyB, version=Vrsn_1_0).parse(
            ims=bytearray(habA.endorse(serder=serder, last=False, framed=True,
                                       gvrsn=Vrsn_1_0)),
            kvy=kvyB)

        cues = [c for c in kvyB.cues if c["kin"] == "bare"]
        assert len(cues) == 1
        assert cues[0]["said"] == other
        assert not [c for c in cues if c["said"] == wanted], \
            "unrelated bar was matched to the outstanding prod"


def test_tampered_bar_is_rejected():
    """A competent tamper — value changed AND outer SAID repaired — must fail.

    The outer `bar` SAID being valid proves only that the sender is
    self-consistent. Authority is the anchor, so the check that matters is
    that the disclosed body re-derives to the SAID the seal committed to.
    """
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        sad = dict(d="", line_of_business="auto", jurisdiction="UT")
        said, saidified = anchorSad(habA, sad)

        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        giveKel(habA, kvyB)

        # A lies: different body, filed under the requested SAID, outer SAID rebuilt
        forged = dict(saidified)
        forged["jurisdiction"] = "CA"
        forgedSerder = bare(pre=habA.pre, route="sealed",
                            data={said: forged}, pvrsn=Vrsn_1_0, kind=Kinds.json)
        forgedMsg = habA.endorse(serder=forgedSerder, last=False, framed=True,
                                 gvrsn=Vrsn_1_0)

        parsing.Parser(kvy=kvyB, version=Vrsn_1_0).parse(
            ims=bytearray(forgedMsg), kvy=kvyB)

        assert not [c for c in kvyB.cues if c["kin"] == "bare"], \
            "tampered bar was accepted"


def test_bar_with_unkeyed_a_block_is_rejected():
    """`bare()` does not enforce the SAID-keyed `a` its own docstring describes.

    `tests/core/test_bare.py` passes a flat dict and gets a flat `a`. The
    keying is the only correlation handle back to the `pro`, so the receiver
    must enforce what the builder does not.
    """
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))

        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        giveKel(habA, kvyB)

        # flat `a` — the shape test_bare.py:94 produces
        flatSerder = bare(pre=habA.pre, route="sealed", data=dict(saidified),
                          pvrsn=Vrsn_1_0, kind=Kinds.json)
        flatMsg = habA.endorse(serder=flatSerder, last=False, framed=True,
                               gvrsn=Vrsn_1_0)

        parsing.Parser(kvy=kvyB, version=Vrsn_1_0).parse(
            ims=bytearray(flatMsg), kvy=kvyB)

        assert not [c for c in kvyB.cues if c["kin"] == "bare"], \
            "bar with an un-keyed `a` block was accepted"


def test_bar_with_undigestable_body_is_rejected():
    """A body with no `d` field cannot re-derive, so it cannot be checked.

    This drives the narrow `except` in processBar. It is here because that
    clause referenced a name that was not imported — it would have raised
    NameError the first time it fired, and no other test reached it.
    """
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        hbyB.makeHab(name="requester")

        said, _ = anchorSad(habA, dict(d="", mandate="auto/UT"))

        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        giveKel(habA, kvyB)

        # SAD-shaped, SAID-keyed, but no `d` field to derive against
        serder = bare(pre=habA.pre, route="sealed",
                      data={said: dict(mandate="auto/UT")},
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        msg = habA.endorse(serder=serder, last=False, framed=True,
                           gvrsn=Vrsn_1_0)

        parsing.Parser(kvy=kvyB, version=Vrsn_1_0).parse(
            ims=bytearray(msg), kvy=kvyB)

        assert not [c for c in kvyB.cues if c["kin"] == "bare"], \
            "undigestable body was accepted"


def test_bar_for_unanchored_said_is_not_cued():
    """An unsolicited `bar` for something the discloser never anchored is not
    authority — the anchor is. It must not surface as a verified disclosure."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        hbyB.makeHab(name="requester")

        _, saidified = coring.Saider.saidify(sad=dict(d="", mandate="auto/UT"))
        said = saidified["d"]  # never anchored by A

        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        giveKel(habA, kvyB)

        serder = bare(pre=habA.pre, route="sealed", data={said: saidified},
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        msg = habA.endorse(serder=serder, last=False, framed=True,
                           gvrsn=Vrsn_1_0)

        parsing.Parser(kvy=kvyB, version=Vrsn_1_0).parse(
            ims=bytearray(msg), kvy=kvyB)

        assert not [c for c in kvyB.cues if c["kin"] == "bare"], \
            "unanchored bar was accepted as a verified disclosure"


def test_unsolicited_bar_is_accepted_when_anchored():
    """A bar MAY be unsolicited. Authority is the anchor, not having asked —
    so a pushed bar verifies exactly as a solicited one does."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))

        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        giveKel(habA, kvyB)

        # A pushes without ever having been prodded
        serder = bare(pre=habA.pre, route="sealed", data={said: saidified},
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        msg = habA.endorse(serder=serder, last=False, framed=True,
                           gvrsn=Vrsn_1_0)

        parsing.Parser(kvy=kvyB, version=Vrsn_1_0).parse(
            ims=bytearray(msg), kvy=kvyB)

        cues = [c for c in kvyB.cues if c["kin"] == "bare"]
        assert len(cues) == 1
        assert cues[0]["said"] == said


def test_allowList_policy_gates_by_requester():
    """The audience gate is per requester, not just per SAD."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        stranger = ProdResponder(hab=habA, kvy=kvyA,
                                 disclosable={said: saidified},
                                 policy=allowList("EOtherAidEntirely"))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)
        assert stranger.service() == bytearray()

        known = ProdResponder(hab=habA, kvy=kvyA,
                              disclosable={said: saidified},
                              policy=allowList(habB.pre))
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)
        assert known.service(), "allowListed requester was refused"


def test_responder_preserves_other_cues():
    """Sharing a cue deck with receipting doers must not eat their cues."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)  # this leaves receipt cues behind

        before = [c["kin"] for c in kvyA.cues if c["kin"] != "prod"]
        assert before, "expected non-prod cues to be present"

        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified},
                                  policy=openPolicy)
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)
        responder.service()

        after = [c["kin"] for c in kvyA.cues]
        assert after == before, "responder consumed cues it does not own"


def test_prod_responder_doer_sends_on_recur():
    """The doer wrapper hands serviced bars to its transport, and stays quiet
    when there is nothing to disclose."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        sent = []
        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified},
                                  policy=openPolicy)
        doer = ProdResponderDoer(responder=responder, send=sent.append)

        assert doer.recur(tyme=0.0) is False
        assert sent == [], "sent something with no prod pending"

        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)
        assert doer.recur(tyme=1.0) is False
        assert len(sent) == 1 and sent[0], "doer did not send the bar"


def test_pro_signed_with_last_indexed_sig_group():
    """hab.endorse(last=True) attaches an lsgs group rather than tsgs.

    Both are legitimate on a `pro`; the parser branch must normalize either.
    Every other test here exercises the tsgs path, so without this one half
    that branch never runs.
    """
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        serder = prod(pre=habB.pre, route="sealed", query=dict(d=said),
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        msg = habB.endorse(serder=serder, last=True, framed=True,
                           gvrsn=Vrsn_1_0)

        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified},
                                  policy=openPolicy)
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(msg), kvy=kvyA)

        assert responder.service(), "lsgs-signed pro was not authenticated"


def test_pro_with_no_attached_signatures_is_refused_by_the_parser():
    """The parser refuses an unsigned pro before Kevery ever sees it."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, _ = anchorSad(habA, dict(d="", mandate="auto/UT"))
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        serder = prod(pre=habB.pre, route="sealed", query=dict(d=said),
                      pvrsn=Vrsn_1_0, kind=Kinds.json)

        with pytest.raises(ValidationError):
            parser = parsing.Parser(kvy=kvyA, version=Vrsn_1_0)
            parser.msgProcess(exts=dict(serder=serder, sigers=[], cigars=[],
                                        tsgs=[], lsgs=[], wigers=[], ssts=[],
                                        sscs=[], local=False),
                              kvy=kvyA, tvy=None, exc=None, rvy=None, vry=None)


def test_parser_branch_without_a_kevery_raises():
    """Defensive branch copied from the qry path: no Kevery, no processing."""
    with habbing.openHby(name="req", temp=True) as hbyB:
        habB = hbyB.makeHab(name="requester")
        serder = prod(pre=habB.pre, route="sealed", query=dict(d="E" + "A" * 43),
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        sigers = habB.sign(ser=serder.raw, indexed=True)

        with pytest.raises(ValidationError):
            parsing.Parser(version=Vrsn_1_0).msgProcess(
                exts=dict(serder=serder, sigers=sigers, cigars=[], tsgs=[],
                          lsgs=[(coring.Prefixer(qb64=habB.pre), sigers)],
                          wigers=[], ssts=[], sscs=[], local=False),
                kvy=None, tvy=None, exc=None, rvy=None, vry=None)


def test_escrowed_pro_logs_at_trace_level():
    """The QueryNotFoundError log branch has a TRACE arm and an error arm.

    Only the error arm ran under the default level, so the TRACE arm was dead
    in test even though it is the arm that prints the message body an operator
    would actually debug from.
    """
    import logging as _logging

    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")
        hbyA.makeHab(name="unused")

        _, saidified = coring.Saider.saidify(sad=dict(d="", mandate="auto/UT"))
        said = saidified["d"]  # never anchored -> escrows -> QueryNotFoundError

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        prior = parsing.logger.level
        parsing.logger.setLevel(_logging.TRACE)
        try:
            parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
                ims=bytearray(buildPro(habB, said)), kvy=kvyA)
        finally:
            parsing.logger.setLevel(prior)

        assert list(hbyA.db.qnfs.getTopItemIter(keys=b'')), "pro was not escrowed"


def test_pro_from_unknown_sender_is_rejected():
    """No key state for the requester means no way to authenticate them."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        # deliberately do NOT give A B's KEL

        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified},
                                  policy=openPolicy)
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)

        assert responder.service() == bytearray(), "answered an unknown sender"


def test_pro_without_requested_said_is_rejected():
    """An empty q block asks for nothing."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")
        anchorSad(habA, dict(d="", mandate="auto/UT"))

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        serder = prod(pre=habB.pre, route="sealed", query=dict(),
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        msg = habB.endorse(serder=serder, last=False, framed=True,
                           gvrsn=Vrsn_1_0)
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(msg), kvy=kvyA)

        assert not [c for c in kvyA.cues if c["kin"] == "prod"]


def test_bar_with_empty_a_block_is_rejected():
    """A bar that discloses nothing is not a disclosure."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        hbyB.makeHab(name="requester")
        anchorSad(habA, dict(d="", mandate="auto/UT"))

        kvyB = eventing.Kevery(db=hbyB.db, lax=True, local=False)
        giveKel(habA, kvyB)

        serder = bare(pre=habA.pre, route="sealed", data=dict(),
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        msg = habA.endorse(serder=serder, last=False, framed=True,
                           gvrsn=Vrsn_1_0)
        parsing.Parser(kvy=kvyB, version=Vrsn_1_0).parse(
            ims=bytearray(msg), kvy=kvyB)

        assert not [c for c in kvyB.cues if c["kin"] == "bare"]


def test_pro_signed_by_nontransferable_aid_authenticates_via_cigar():
    """A non-transferable requester signs with an unindexed cigar, not an
    indexed sig group. That is still an authenticated requester."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester", transferable=False,
                            icount=1, ncount=0, isith='1', nsith='0')

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified},
                                  policy=openPolicy)
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)

        assert responder.service(), "cigar-signed pro was not authenticated"


# --- error branches the parser rejects before Kevery sees them --------------
# These call the handler directly on purpose. The *behavioural* rules above are
# all driven from the wire; these only cover defensive branches that a wire
# message cannot reach because the parser refuses it first.

def test_authenticateMsg_rejects_unsigned_message():
    with habbing.openHby(name="disc", temp=True) as hby:
        hab = hby.makeHab(name="discloser")
        kvy = eventing.Kevery(db=hby.db, lax=True, local=False)
        serder = prod(pre=hab.pre, route="sealed", query=dict(d="E" + "A" * 43),
                      pvrsn=Vrsn_1_0, kind=Kinds.json)

        with pytest.raises(ValidationError):
            kvy.authenticateMsg(serder)


def test_authenticateMsg_rejects_bad_signature():
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        serder = prod(pre=habB.pre, route="sealed", query=dict(d="E" + "A" * 43),
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        other = prod(pre=habB.pre, route="different",
                     query=dict(d="E" + "B" * 43), pvrsn=Vrsn_1_0,
                     kind=Kinds.json)
        # signatures over a DIFFERENT message
        sigers = habB.sign(ser=other.raw, indexed=True)

        with pytest.raises(ValidationError):
            kvy_source = coring.Prefixer(qb64=habB.pre)
            kvyA.authenticateMsg(serder, source=kvy_source, sigers=sigers)


def test_authenticateMsg_rejects_missing_or_mismatched_est_event():
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        serder = prod(pre=habB.pre, route="sealed", query=dict(d="E" + "A" * 43),
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        sigers = habB.sign(ser=serder.raw, indexed=True)
        source = coring.Prefixer(qb64=habB.pre)

        # est event at an sn that does not exist
        with pytest.raises(ValidationError):
            kvyA.authenticateMsg(serder, source=source, sigers=sigers,
                                 seqner=coring.Number(num=99))

        # est event exists but the referenced SAID is not it
        wrong = coring.Saider(qb64="E" + "C" * 43)
        with pytest.raises(ValidationError):
            kvyA.authenticateMsg(serder, source=source, sigers=sigers,
                                 seqner=coring.Number(num=0), ssaider=wrong)


def test_client_policy_can_gate_on_an_authorizing_credential_in_q():
    """The AuthZ seam must be able to see what the requester actually sent.

    This is the shape upstream issue #520 anticipates: the prod carries a
    reference to an authorizing ACDC, and the responder decides from it. The
    reference goes in `q` rather than at top level, because `q` is a free-form
    modifier block while a new top-level field would violate the spec's "No
    other top-level fields are allowed (MUST NOT appear)".

    Written as a *client* would write it -- a plain callable, no subclassing --
    and driven from the wire, so it proves the seam rather than describing it.
    Before `serder` was passed to the policy this test could not be written at
    all: the 'az' value was unreachable from inside a policy.
    """
    GRANTED = "EHxsFOAkc6TCjLm-3IPPMlU8O6z2NNlWyRTUwuPBRT4k"

    def credentialPolicy(source, said, route, serder):
        # A real one would verify the chain roots to the EGF authorities[] and
        # that the TEL says not-revoked AT REQUEST TIME. Here we only need to
        # prove the seam hands over enough to make that decision.
        return (serder.ked.get("q") or {}).get("az") == GRANTED

    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))
        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified},
                                  policy=credentialPolicy)

        def prodWith(az):
            q = dict(d=said)
            if az is not None:
                q["az"] = az
            serder = prod(pre=habB.pre, route="sealed", query=q,
                          pvrsn=Vrsn_1_0, kind=Kinds.json)
            return habB.endorse(serder=serder, last=False, framed=True,
                                gvrsn=Vrsn_1_0)

        # no credential referenced -> refused
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(prodWith(None)), kvy=kvyA)
        assert responder.service() == bytearray()

        # wrong credential -> refused
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(prodWith("E" + "Z" * 43)), kvy=kvyA)
        assert responder.service() == bytearray()

        # the granted credential -> disclosed
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(prodWith(GRANTED)), kvy=kvyA)
        assert responder.service(), "credentialed requester was refused"


def test_a_raising_policy_fails_closed_and_does_not_stall_the_drain():
    """Caller code gets to be buggy without becoming a disclosure bug.

    A credential-verifying policy has many ways to raise -- missing registry,
    unresolvable chain, malformed reference. That must deny, and must not
    abort the rest of the cue drain.
    """
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        boom, boomSad = anchorSad(habA, dict(d="", mandate="auto/UT"))
        fine, fineSad = anchorSad(habA, dict(d="", mandate="home/CA"))

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        def flaky(source, said, route, serder):
            if said == boom:
                raise RuntimeError("registry unreachable")
            return True

        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={boom: boomSad, fine: fineSad},
                                  policy=flaky)

        for target in (boom, fine):
            parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
                ims=bytearray(buildPro(habB, target)), kvy=kvyA)

        msgs = responder.service()
        assert msgs, "the raising request stalled the whole drain"
        # exactly one bar came back, and it is for the SAD whose policy answered
        assert bytes(msgs).count(b'"t":"bar"') == 1
        assert fine.encode() in bytes(msgs)
        assert boom.encode() not in bytes(msgs), "raising policy disclosed"


def test_unauthorized_pro_gets_silence():
    """Default closed. An unauthorized `pro` gets nothing back, and the wire
    response is indistinguishable from 'I do not have that SAID'.

    An explicit refusal would be an existence oracle: it would confirm to an
    unauthorized party that a given SAID is anchored here, which is exactly the
    metadata a SAID-based commitment is supposed to withhold.
    """
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", secret="not yours"))

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        # A holds the SAD but has NOT marked it disclosable
        responder = ProdResponder(hab=habA, kvy=kvyA, disclosable={},
                                  policy=openPolicy)
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)
        withheld = responder.service()

        # A holds nothing at all for an unknown SAID
        unknown = "EBcIURLpxmVwahksgrsGW6_dUw0zBhyEHYFk17eWrZfk"
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, unknown)), kvy=kvyA)
        notFound = responder.service()

        assert withheld == bytearray(), "withheld SAD produced a response"
        assert withheld == notFound, \
            "refusal is distinguishable from not-found — an existence oracle"


def test_default_policy_denies_even_disclosable_sads():
    """Opening the door must be a deliberate act, not a default."""
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        # marked disclosable, but NO policy passed -> still denied
        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified})
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)

        assert responder.service() == bytearray(), \
            "default policy disclosed a SAD"


def test_pro_for_unanchored_said_is_escrowed():
    """A `pro` for a seal A has not anchored yet goes to escrow, not the bin.

    Follows the query-not-found house pattern: escrow, then re-process on a
    later pass once the anchor lands.
    """
    with habbing.openHby(name="disc", temp=True) as hbyA, \
            habbing.openHby(name="req", temp=True) as hbyB:
        habA = hbyA.makeHab(name="discloser")
        habB = hbyB.makeHab(name="requester")

        sad = dict(d="", mandate="auto/UT")
        _, saidified = coring.Saider.saidify(sad=dict(sad))
        said = saidified["d"]  # NOT anchored yet

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        giveKel(habB, kvyA)

        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified},
                                  policy=openPolicy)
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(buildPro(habB, said)), kvy=kvyA)

        assert responder.service() == bytearray(), "answered before anchoring"
        assert list(hbyA.db.qnfs.getTopItemIter(keys=b'')), \
            "pro was dropped, not escrowed"

        # now A anchors it, and the escrow drains into a real answer
        habA.interact(data=[dict(d=said)])
        kvyA.processQueryNotFound()

        assert responder.service(), "escrowed pro never answered after anchoring"


def test_unsigned_pro_is_rejected():
    """Disclosure decisions need an authenticated requester."""
    with habbing.openHby(name="disc", temp=True) as hbyA:
        habA = hbyA.makeHab(name="discloser")
        said, saidified = anchorSad(habA, dict(d="", mandate="auto/UT"))

        kvyA = eventing.Kevery(db=hbyA.db, lax=True, local=False)
        responder = ProdResponder(hab=habA, kvy=kvyA,
                                  disclosable={said: saidified},
                                  policy=openPolicy)

        serder = prod(pre=habA.pre, route="sealed", query=dict(d=said),
                      pvrsn=Vrsn_1_0, kind=Kinds.json)
        parsing.Parser(kvy=kvyA, version=Vrsn_1_0).parse(
            ims=bytearray(serder.raw), kvy=kvyA)  # no signature attached

        assert responder.service() == bytearray(), "unsigned pro was answered"


def test_a_credential_issuance_seal_is_found_as_an_anchor():
    """A `pro` for a really-issued credential must reach a cue.

    Credential issuance anchors a THREE-field SealEvent(i, s, d) into the KEL
    (vdr/eventing.py:348,402; credentialing.py:628 passes pre=vcid,
    regd=iserder.said -- so `i` is the ACDC SAID and `d` is the TEL event's).
    `Kevery.anchoringPre` asked `fetchLastSealingEventBySeal(seal=dict(d=said))`,
    which guards on `tuple(eseal) == Seal._fields` == ('d',) -- so a 3-field
    seal never matched and every issued credential looked UNANCHORED.
    processPro then raised QueryNotFoundError and pushed no cue, so no
    responder could answer. Measured live: 538 prods sent, 0 replies.

    Every pre-existing fixture in this file anchors a bare {'d': said} seal,
    which is exactly why this shipped green.
    """
    from keri.app import habbing
    from keri.core import eventing as evt

    with habbing.openHby(name="anchsl", temp=True) as hby:
        hab = hby.makeHab(name="issuer")

        acdcSaid = "E" + "A" * 43        # stands in for the credential SAID
        telSaid = "E" + "B" * 43         # stands in for the TEL iss event SAID
        # The shape credentialing.py actually writes.
        hab.interact(data=[dict(i=acdcSaid, s="0", d=telSaid)])

        kvy = evt.Kevery(db=hby.db, lax=True, local=False)

        assert kvy.anchoringPre(said=telSaid, pres=[hab.pre]) == hab.pre, (
            "the seal's own d was not found -- anchor lookup is broken")
        assert kvy.anchoringPre(said=acdcSaid, pres=[hab.pre]) == hab.pre, (
            "the credential SAID carried in the seal's i was not found")
        assert kvy.anchoringPre(said="E" + "Z" * 43, pres=[hab.pre]) is None, (
            "an uncommitted SAID must NOT look anchored")


def test_a_bare_digest_seal_still_resolves():
    """The old shape must keep working -- every other fixture here uses it."""
    from keri.app import habbing
    from keri.core import eventing as evt

    with habbing.openHby(name="anchbare", temp=True) as hby:
        hab = hby.makeHab(name="issuer")
        said = "E" + "C" * 43
        hab.interact(data=[dict(d=said)])

        kvy = evt.Kevery(db=hby.db, lax=True, local=False)
        assert kvy.anchoringPre(said=said, pres=[hab.pre]) == hab.pre
