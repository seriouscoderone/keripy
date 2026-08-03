# -*- encoding: utf-8 -*-
"""Tests for sealing module — verifying a body against a seal."""
import json
from keri.core import coring


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
