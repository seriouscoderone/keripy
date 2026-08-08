"""`serializeMessage` conflated the message body with its CESR attachments.

FORK FIX 2026-08-08. Upstream seeded its `atc` accumulator with `exn.raw` — the
JSON body — and then applied two rules that hold for CESR attachments and not for
a JSON body:

  1. `if len(atc) % 4: raise ValueError("Invalid attachments size=...,
     nonintegral quadlets.")`. A JSON body's length mod 4 is arbitrary, so this
     raised on roughly three exns in four. Measured live in Locksmith:
     "Invalid attachments size=489, nonintegral quadlets." on every IPEX
     admit-back (489 % 4 == 1).
  2. For `framed=False` the AttachmentGroup counter was sized over
     body+attachments and emitted BEFORE the body.

There were no tests for this function at all, which is how it survived. It also
returned `(None, None)` for a missing exn while returning a bare bytearray on
success — and a 2-tuple is TRUTHY, so `if not atc:` guards did not catch it.
"""
from hio.help import decking

from keri import Kinds, Vrsn_1_0
from keri.app import openHby
from keri.core import Counter, Salter, parsing, serdering
from keri.core.coring import MtrDex
from keri.core.counting import Codens
from keri.peer import Exchanger
from keri.peer.exchanging import serializeMessage
from keri.vdr import incept

TEST_VERSION = Vrsn_1_0


def _stored_exn(hby, hab):
    """An exn and its signatures in the db, put there the way production does.

    Parsed through an Exchanger rather than `exns.put`-ed directly: `serializeMessage`
    rebuilds the attachment from `hby.db.esigs`, so an exn stored without its
    signatures makes `verify()` raise MissingSignatureError long before the code
    under test runs. Hand-storing only the body is exactly the simplified-fixture
    trap — the artifact has to be the real one.
    """
    kwa = dict(version=TEST_VERSION, kind=Kinds.json)
    regser = incept(hab.pre, baks=[], toad=0, cnfg=[],
                    nonce="AH3-1EZWXU9I0fv3Iz_9ZIhjj13JO7u4GNFYC3-l8_K-",
                    code=MtrDex.Blake3_256, **kwa)
    seal = dict(i=regser.pre, s=regser.sn, d=regser.said)
    ixn = hab.interact(data=[seal], framed=True, gvrsn=TEST_VERSION, **kwa)
    raw = hab.exchange(route="/multisig/registry/incept",
                       attributes=dict(m="Let's create a registry"),
                       embeds=dict(vcp=regser.raw, ixn=ixn),
                       framed=True, gvrsn=TEST_VERSION, **kwa)

    exc = Exchanger(hby=hby, handlers=[])
    exc.addHandler(_Sink("/multisig/registry/incept"))
    parsing.Parser(version=TEST_VERSION).parseOne(ims=bytearray(raw), exc=exc)

    serder = serdering.SerderKERI(raw=bytes(raw))
    assert hby.db.exns.get(keys=(serder.said,)) is not None, (
        "the exn did not persist — the fixture is not exercising the real path")
    return serder


class _Sink:
    """Minimal exn handler: the Exchanger drops a route it cannot resolve, and a
    dropped exn is never persisted."""

    def __init__(self, resource):
        self.resource = resource
        self.msgs = decking.Deck()


def test_a_body_whose_length_is_not_a_multiple_of_four_still_serializes():
    """The whole defect: this raised whenever len(exn.raw) % 4 != 0."""
    with openHby(salt=Salter(raw=b'0123456789abcdef').qb64) as hby:
        hab = hby.makeHab(name="test", version=TEST_VERSION, kind=Kinds.json)
        exn = _stored_exn(hby, hab)

        # Guard the guard: if this body happened to be 4-aligned the test would
        # pass without exercising anything.
        assert len(exn.raw) % 4 != 0, (
            f"this fixture's body is {len(exn.raw)} bytes, already 4-aligned — "
            "it cannot demonstrate the defect; change the attributes to shift it")

        msg = serializeMessage(hby, exn.said, framed=True)
        assert msg is not None and len(msg) >= len(exn.raw)


def test_framed_output_starts_with_the_body_and_adds_no_counter():
    """`framed=True` bytes are UNCHANGED by the fix — body then attachments, no
    attachment group. Pinned so a later edit cannot quietly reframe a stream that
    peers are already parsing."""
    with openHby(salt=Salter(raw=b'0123456789abcdef').qb64) as hby:
        hab = hby.makeHab(name="test", version=TEST_VERSION, kind=Kinds.json)
        exn = _stored_exn(hby, hab)

        msg = serializeMessage(hby, exn.said, framed=True)
        assert bytes(msg[:len(exn.raw)]) == bytes(exn.raw)
        group = Counter(Codens.AttachmentGroup, count=1, version=Vrsn_1_0).qb64b[:2]
        assert bytes(msg[len(exn.raw):len(exn.raw) + 2]) != bytes(group), (
            "framed=True must not emit an attachment group counter")


def test_unframed_puts_the_counter_after_the_body_sized_to_the_attachments():
    """Upstream emitted `[counter][body][attachments]` with the counter sized over
    body+attachments — a count describing the wrong bytes, in the wrong place.
    Correct is `[body][counter][attachments]`, which is also what makes a caller
    that strips `exn.size` bytes off the front land exactly on the counter
    (locksmith core/ipexing.py's group-multisig admit path does precisely that)."""
    with openHby(salt=Salter(raw=b'0123456789abcdef').qb64) as hby:
        hab = hby.makeHab(name="test", version=TEST_VERSION, kind=Kinds.json)
        exn = _stored_exn(hby, hab)

        framed = serializeMessage(hby, exn.said, framed=True)
        unframed = serializeMessage(hby, exn.said, framed=False)
        attachments = bytes(framed[len(exn.raw):])

        assert bytes(unframed[:len(exn.raw)]) == bytes(exn.raw), (
            "the body must come first; a counter in front of it is not a CESR "
            "message stream")
        tail = bytes(unframed[len(exn.raw):])
        expected = Counter(Codens.AttachmentGroup,
                           count=len(attachments) // 4,
                           version=Vrsn_1_0).qb64b
        assert tail == bytes(expected) + attachments, (
            "the counter must sit between body and attachments and size the "
            "attachments alone")


def test_a_missing_exn_returns_a_falsy_none_not_a_truthy_tuple():
    """`(None, None)` is TRUTHY, so every `if not atc:` guard in every caller
    sailed past the not-found case and failed further from the cause."""
    with openHby(salt=Salter(raw=b'0123456789abcdef').qb64) as hby:
        hby.makeHab(name="test", version=TEST_VERSION, kind=Kinds.json)
        result = serializeMessage(hby, "E" + "Z" * 43, framed=True)
        assert result is None
        assert not result


def test_a_genuinely_misaligned_attachment_still_raises():
    """The quadlet check must survive the fix — it just has to measure the
    attachments. Corrupt one and the error comes back."""
    import pytest

    with openHby(salt=Salter(raw=b'0123456789abcdef').qb64) as hby:
        hab = hby.makeHab(name="test", version=TEST_VERSION, kind=Kinds.json)
        exn = _stored_exn(hby, hab)

        # A pathed component of non-quadlet length is the one attachment source a
        # caller can corrupt without forging a signature.
        hby.db.epath.add(keys=(exn.said,), val=b"abc")
        with pytest.raises(ValueError, match="nonintegral"):
            serializeMessage(hby, exn.said, framed=True)
