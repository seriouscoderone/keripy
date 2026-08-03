# -*- encoding: utf-8 -*-
"""
tests.app.test_prod_client module

Tests for ProdClient, the requesting half of the Prod ('pro') / Bare ('bar')
content-retrieval pair. ProdResponder (tests/core/test_prod_bare.py) answers
prods; this drives the other end of the wire -- building a signed `pro` and
harvesting a received `bar` by the SAID that was requested, not by trusting
arrival order or the sender's say-so.
"""
import json

from keri.app import habbing
from keri.core import coring, eventing, parsing, serdering


def test_client_builds_a_signed_pro_that_reparses_to_the_same_said():
    with habbing.openHby(name="client", temp=True) as hby:
        hab = hby.makeHab(name="asker")
        said = coring.Diger(ser=b'{"x":1}').qb64

        from keri.app.prodding import ProdClient
        raw = ProdClient(hab=hab).request(pre=hab.pre, said=said)

        serder = serdering.SerderKERI(raw=raw)
        assert serder.ilk == "pro"
        assert serdering.SerderKERI(raw=serder.raw).said == serder.said


def test_client_carries_az_in_the_q_block_without_changing_the_field_domain():
    with habbing.openHby(name="az", temp=True) as hby:
        hab = hby.makeHab(name="asker")
        said = coring.Diger(ser=b'{"x":2}').qb64

        from keri.app.prodding import ProdClient
        raw = ProdClient(hab=hab).request(pre=hab.pre, said=said, az="ECredentialSaid")

        serder = serdering.SerderKERI(raw=raw)
        assert serder.ked["q"]["az"] == "ECredentialSaid"
        assert "az" not in serder.ked          # never at top level


def test_harvest_returns_the_body_for_the_requested_said_and_none_otherwise():
    with habbing.openHby(name="harvest", temp=True) as hby:
        hab = hby.makeHab(name="discloser")
        body = {"kind": "mandate"}
        raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
        said = coring.Diger(ser=raw).qb64

        serder = eventing.bare(pre=hab.pre, route="/sealed", data={said: body})

        from keri.app.prodding import ProdClient
        client = ProdClient(hab=hab)
        assert client.harvest(serder=serder, said=said) == body
        assert client.harvest(serder=serder, said="EOtherSaid") is None


def test_bare_does_not_enforce_said_keying_of_its_a_block():
    """Verification for harvest()'s own justification, driven against bare()
    directly rather than assumed from its docstring.

    eventing.bare()'s docstring describes `a` as "dict of dicts of comitted
    SADS for SAIDs in seals keyed by SAID" -- but bare() never checks that.
    It builds `a` from whatever dict `data` is, verbatim. Proof: file a body
    under a key that provably is NOT that body's own SAID (computed
    independently here via the same Diger the real SAID would use) and
    confirm bare() still builds a valid message with that mismatched keying
    intact, rather than raising or silently correcting the key. This is the
    reason ProdClient.harvest() checks isinstance(..., dict) on a dict.get()
    lookup instead of trusting that whatever is filed under a requested SAID
    is that SAID's own disclosure.
    """
    with habbing.openHby(name="bare_keying", temp=True) as hby:
        hab = hby.makeHab(name="discloser")

        body = {"kind": "mandate"}
        raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
        actualSaid = coring.Diger(ser=raw).qb64
        wrongKey = "ENotTheRealSaidAtAllForThisBody0000000000"
        assert wrongKey != actualSaid

        serder = eventing.bare(pre=hab.pre, route="/sealed",
                                data={wrongKey: body})

        # bare() accepted the mismatch outright: no exception above, and the
        # observed effect is that the wrong key -- not the real SAID -- is
        # what actually keys the `a` block.
        assert serder.ked["a"] == {wrongKey: body}
        assert actualSaid not in serder.ked["a"], \
            "bare() re-keyed the body under its real SAID -- it should not"

        # the message is otherwise well-formed: it reparses and re-verifies,
        # so this is not merely an artifact of skipped validation elsewhere.
        reparsed = serdering.SerderKERI(raw=serder.raw)
        assert reparsed.said == serder.said
