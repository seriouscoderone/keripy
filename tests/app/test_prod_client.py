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
from keri.core import coring, eventing, serdering


def test_client_builds_a_signed_pro_that_reparses_to_the_same_said():
    with habbing.openHby(name="client", temp=True) as hby:
        hab = hby.makeHab(name="asker")
        said = coring.Diger(ser=b'{"x":1}').qb64

        from keri.app.prodding import ProdClient
        raw = ProdClient(hab=hab).request(pre=hab.pre, said=said)

        serder = serdering.SerderKERI(raw=raw)
        assert serder.ilk == "pro"
        assert serdering.SerderKERI(raw=serder.raw).said == serder.said
        assert serder.ked["q"]["d"] == said     # the ask actually carries the said
        assert serder.ked["r"] == "/sealed"     # and the default route


def test_the_target_pre_is_a_routing_selector_and_not_on_the_wire():
    """`pre` addresses the request; it deliberately does not enter the signed bytes.

    A `pro` has no recipient field -- prod(pre=...) is the SENDER. Two requests
    for the same said differ only in delivery, never in content. If this ever
    starts failing, the pro message gained a recipient field and ProdClient
    must be revisited.
    """
    with habbing.openHby(name="routing", temp=True) as hby:
        hab = hby.makeHab(name="asker")
        said = coring.Diger(ser=b'{"x":3}').qb64

        from keri.app.prodding import ProdClient
        client = ProdClient(hab=hab)
        to_x = serdering.SerderKERI(raw=client.request(pre="EPeerX", said=said))
        to_y = serdering.SerderKERI(raw=client.request(pre="EPeerY", said=said))

        assert to_x.ked["i"] == hab.pre                  # sender, not target
        assert to_y.ked["i"] == hab.pre
        assert "EPeerX" not in to_x.raw.decode()         # target is nowhere on the wire
        assert "EPeerY" not in to_y.raw.decode()
        assert to_x.ked["q"] == to_y.ked["q"]            # same ask either way


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


def test_harvest_rejects_a_non_dict_value_under_the_right_key():
    """The isinstance guard's real job: right key, wrong value type.

    Without this the guard could be weakened to `found is not None` and no
    test would notice.
    """
    with habbing.openHby(name="nondict", temp=True) as hby:
        hab = hby.makeHab(name="discloser")
        said = coring.Diger(ser=b'{"k":1}').qb64

        from keri.app.prodding import ProdClient
        client = ProdClient(hab=hab)
        for junk in ("not-a-dict", ["not", "a", "dict"], 42):
            serder = eventing.bare(pre=hab.pre, route="/sealed", data={said: junk})
            assert client.harvest(serder=serder, said=said) is None


def test_harvest_returns_none_when_the_a_block_itself_is_not_a_dict():
    """harvest() must not raise when `a` itself is malformed, not just its values.

    `serdering._verify()` checks field NAMES only, never value TYPES (it only
    diffs the SAD's top-level KEYS against the FieldDom's allowed labels), so a
    `bar` whose `a` is a list rather than a dict is not rejected anywhere
    upstream -- confirmed directly: `eventing.bare(data=["not","a","dict"])`
    builds and returns a normal, well-formed serder with `ked["a"]` literally a
    list. The old `data = serder.ked.get("a") or {}` line would then call
    `.get(said)` on that list and raise AttributeError -- verified live against
    the pre-fix code before this fix was written. harvest() must hold the same
    never-raises-on-malformed-input line `verifySealedBody` (Task 3) holds.
    """
    with habbing.openHby(name="listA", temp=True) as hby:
        hab = hby.makeHab(name="discloser")
        said = coring.Diger(ser=b'{"k":2}').qb64

        serder = eventing.bare(pre=hab.pre, route="/sealed",
                                data=["not", "a", "dict", "at", "all"])
        assert isinstance(serder.ked["a"], list), \
            "bare() no longer accepts a list a-block -- this test's premise changed"

        from keri.app.prodding import ProdClient
        client = ProdClient(hab=hab)
        assert client.harvest(serder=serder, said=said) is None


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
