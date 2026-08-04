# -*- encoding: utf-8 -*-
"""Tests for sealing module — verifying a body against a seal."""
import json

from keri.app import habbing
from keri.core import coring
from keri.kering import Vrsn_1_0, Vrsn_2_0
from keri.vc import proving

SCHEMA = "EBfdlu8R27Fbx-ehrqwImnK-8Cm79sqbAQ4MmvEAYqao"


def _acdc(version=Vrsn_2_0, **kwa):
    """A genuine ACDC, built by keripy's own credential factory.

    What is representative: this is a real `SerderACDC`. Its SAID is derived by
    `SerderACDC._compute`, which for v2 runs over the MOST COMPACT variant --
    nested `a`/`e`/`r` blocks replaced by their own SAIDs via Compactor, then
    `v` resized to the compact length. `.verify()` returns True. The issuer is a
    real self-incepted AID, and the credential carries a recipient, an edge
    section and a rule section, so the compaction path is actually exercised.

    What is NOT representative: nothing issues it. There is no TEL registry, no
    `rd`/`ri` status, no issuance event and no anchoring seal in the issuer's
    KEL -- a direct-mode test with no Regery cannot provide those. Those are all
    fields ALONGSIDE the SAID computation, though, not inputs to a different one:
    a registry-backed ACDC differs from this one by having more top-level
    fields, and the derivation over them is the same code path.
    """
    with habbing.openHby(name="acdcfx", temp=True) as hby:
        issuer = hby.makeHab(name="issuer").pre
    return proving.credential(
        issuer=issuer, schema=SCHEMA, version=version,
        data=dict(LEI="254900OPPU84GM83MG36"),
        recipient="EFrOtBOZKKFbBQvKZBLGSgFhVOhqNhNTQmNbmzXKmQQQ",
        source=dict(d="", mandate=dict(n="EBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
                                       s=SCHEMA)),
        rules=dict(d="", usageDisclaimer=dict(l="Usage subject to the mandate.")),
        **kwa)


def _body_and_seal():
    # Keys deliberately NOT in alphabetical order: this is what makes the whole
    # file sensitive to a sort_keys regression in the production serialization.
    body = {"line_of_business": "general_liability", "kind": "mandate"}
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    return body, {"d": coring.Diger(ser=raw).qb64}


def test_a_matching_body_verifies():
    from keri.core.sealing import verifySealedBody
    body, seal = _body_and_seal()
    assert verifySealedBody(seal=seal, body=body) is True


def test_a_tampered_body_is_rejected():
    from keri.core.sealing import verifySealedBody
    body, seal = _body_and_seal()
    body["line_of_business"] = "commercial_auto"
    assert verifySealedBody(seal=seal, body=body) is False


def test_a_substituted_body_is_rejected():
    from keri.core.sealing import verifySealedBody
    _, seal = _body_and_seal()
    assert verifySealedBody(seal=seal, body={"kind": "something-else"}) is False


def test_a_malformed_seal_returns_false_and_does_not_raise():
    from keri.core.sealing import verifySealedBody
    body, _ = _body_and_seal()
    assert verifySealedBody(seal={}, body=body) is False
    assert verifySealedBody(seal={"d": "not-a-said"}, body=body) is False


def test_a_non_dict_seal_returns_false_and_does_not_raise():
    """The seal side gets the same never-raises guarantee as the body side."""
    from keri.core.sealing import verifySealedBody
    body, _ = _body_and_seal()
    assert verifySealedBody(seal="not-a-dict", body=body) is False
    assert verifySealedBody(seal=["not", "a", "dict"], body=body) is False
    assert verifySealedBody(seal=42, body=body) is False


def test_a_bytes_body_verifies():
    from keri.core.sealing import verifySealedBody
    body, seal = _body_and_seal()
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    assert verifySealedBody(seal=seal, body=raw) is True


def test_an_unserializable_body_returns_false():
    from keri.core.sealing import verifySealedBody

    class UnserializableObject:
        pass

    seal = {"d": "EJhSDe9R5olJlcakLv_VJtxHoIpeuldeVv_likVNRVVL"}
    assert verifySealedBody(seal=seal, body=UnserializableObject()) is False


def test_a_saidified_sad_verifies():
    """The case the plain-digest path gets WRONG.

    Every ACDC is a SAD. Its SAID comes from Saider.saidify, which dummies the
    `d` field before digesting -- a plain digest over the finished bytes gives
    a different value. This test fails against a plain-digest-only verifier.
    """
    from keri.core.sealing import verifySealedBody
    sad = dict(d="", line_of_business="general_liability", jurisdiction="US-UT")
    _, saidified = coring.Saider.saidify(sad=dict(sad))
    said = saidified["d"]
    assert verifySealedBody(seal={"d": said}, body=saidified) is True


def test_a_tampered_sad_is_rejected():
    from keri.core.sealing import verifySealedBody
    sad = dict(d="", line_of_business="general_liability", jurisdiction="US-UT")
    _, saidified = coring.Saider.saidify(sad=dict(sad))
    said = saidified["d"]
    tampered = dict(saidified, jurisdiction="US-NV")   # d left stale, as an attacker would
    assert verifySealedBody(seal={"d": said}, body=tampered) is False


def test_a_sad_with_a_forged_d_field_is_rejected():
    """An attacker who rewrites `d` to match their tampered content still fails,
    because the seal's SAID came from the log, not from the body."""
    from keri.core.sealing import verifySealedBody
    sad = dict(d="", line_of_business="general_liability", jurisdiction="US-UT")
    _, saidified = coring.Saider.saidify(sad=dict(sad))
    said = saidified["d"]
    forged = dict(saidified, jurisdiction="US-NV")
    _, reforged = coring.Saider.saidify(sad=dict(forged, d=""))  # self-consistent forgery
    assert reforged["d"] != said
    assert verifySealedBody(seal={"d": said}, body=reforged) is False


# --- the SAD's own `d` field ------------------------------------------------
# Saider._derive overwrites sad[label] with a dummy before digesting, so the
# supplied `d` is never itself checked. A caller that files verified content
# under body["d"], or follows an ACDC edge by it, would file attacker-chosen
# content under an attacker-chosen identity.

def test_a_body_whose_d_disagrees_with_the_seal_is_rejected():
    """Content matches, `d` lies. The re-derivation alone says True."""
    from keri.core.sealing import verifySealedBody
    sad = dict(d="", line_of_business="general_liability", jurisdiction="US-UT")
    _, saidified = coring.Saider.saidify(sad=dict(sad))
    said = saidified["d"]
    lied = dict(saidified, d="EAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert lied["d"] != said
    assert verifySealedBody(seal={"d": said}, body=lied) is False


def test_a_body_with_a_null_or_non_string_d_is_rejected():
    from keri.core.sealing import verifySealedBody
    sad = dict(d="", line_of_business="general_liability", jurisdiction="US-UT")
    _, saidified = coring.Saider.saidify(sad=dict(sad))
    said = saidified["d"]
    assert verifySealedBody(seal={"d": said}, body=dict(saidified, d=None)) is False
    assert verifySealedBody(seal={"d": said}, body=dict(saidified, d=12345)) is False


# --- real ACDCs -------------------------------------------------------------

def test_a_real_v2_acdc_verifies():
    """The honest path this predicate exists for, and got wrong.

    A v2 ACDC's SAID is NOT Saider.saidify's answer: SerderACDC._compute derives
    it over the most compact variant. Every other fixture in this file is a flat
    dict, which is not what an ACDC looks like, so this failure was invisible.
    """
    from keri.core.sealing import verifySealedBody
    acdc = _acdc(version=Vrsn_2_0)
    assert acdc.verify()                       # it is a real, valid ACDC
    assert acdc.sad["v"].startswith("ACDCCAACAA")     # and really is protocol v2
    saidify, _ = coring.Saider.saidify(sad=dict(acdc.sad))
    assert saidify.qb64 != acdc.said, \
        "saidify now agrees with SerderACDC -- this test's premise changed"
    assert verifySealedBody(seal={"d": acdc.said}, body=dict(acdc.sad)) is True


def test_a_tampered_v2_acdc_is_rejected():
    from keri.core.sealing import verifySealedBody
    acdc = _acdc(version=Vrsn_2_0)
    tampered = dict(acdc.sad)
    tampered["a"] = dict(tampered["a"], LEI="000000000000000000")
    assert verifySealedBody(seal={"d": acdc.said}, body=tampered) is False


def test_a_real_v1_acdc_verifies():
    """v1 has no compact variant, so saidify happened to agree. The dispatch
    must not break the case that already worked."""
    from keri.core.sealing import verifySealedBody
    acdc = _acdc(version=Vrsn_1_0)
    assert acdc.sad["v"].startswith("ACDC10")
    assert verifySealedBody(seal={"d": acdc.said}, body=dict(acdc.sad)) is True


def test_a_real_keri_event_sad_verifies():
    """A KERI-protocol SAD needs SerderKERI, not SerderACDC. Anchoring a KEL
    event by its SAID and disclosing the event itself is an ordinary thing to do.
    """
    from keri.core.sealing import verifySealedBody
    with habbing.openHby(name="kerisad", temp=True) as hby:
        hab = hby.makeHab(name="ctrl")
        hab.interact(data=[{"d": coring.Diger(ser=b'{"x":1}').qb64}])
        serder = hab.kever.serder
        assert serder.sad["v"].startswith("KERI")
        assert verifySealedBody(seal={"d": serder.said},
                                body=dict(serder.sad)) is True


def test_an_unrecognized_or_malformed_version_string_fails_closed():
    """A `v` the dispatch cannot resolve must return False, never raise."""
    from keri.core.sealing import verifySealedBody
    acdc = _acdc(version=Vrsn_2_0)
    said = acdc.said
    for vs in ("", "garbage", "XXXX10JSON000000_", 12345, None, [], "KERI10JSON000000_"):
        body = dict(acdc.sad)
        body["v"] = vs
        assert verifySealedBody(seal={"d": said}, body=body) is False


def test_a_body_the_serder_cannot_make_fails_closed():
    """`v` says ACDC but the field map is not one. Serder raises; we return False."""
    from keri.core.sealing import verifySealedBody
    acdc = _acdc(version=Vrsn_2_0)
    body = dict(v=acdc.sad["v"], d=acdc.said, nonsense=["not", "an", "acdc"])
    assert verifySealedBody(seal={"d": acdc.said}, body=body) is False


# --- the opaque path's accepted types ---------------------------------------

def test_bytearray_and_memoryview_bodies_verify():
    """keripy hands bytearrays around everywhere -- Hab.endorse, Parser and
    ProdResponder.service() all return one -- so bytes-only was a live
    honest-path false negative."""
    from keri.core.sealing import verifySealedBody
    body, seal = _body_and_seal()
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    assert verifySealedBody(seal=seal, body=bytearray(raw)) is True
    assert verifySealedBody(seal=seal, body=memoryview(raw)) is True


def test_a_str_body_is_rejected_rather_than_json_encoded():
    """A str used to be JSON-encoded AS A JSON STRING and digested, which is a
    wrong answer with no exception. Encoding is the caller's decision: a str has
    no canonical byte form here, so refuse it."""
    from keri.core.sealing import verifySealedBody
    body, seal = _body_and_seal()
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    assert verifySealedBody(seal=seal, body=raw.decode()) is False
    # and it is refused, not merely mis-digested: the seal over the digest of
    # the str's OWN json encoding does not verify either.
    quoted = json.dumps(raw.decode(), separators=(",", ":"),
                        ensure_ascii=False).encode()
    assert verifySealedBody(seal={"d": coring.Diger(ser=quoted).qb64},
                            body=raw.decode()) is False
