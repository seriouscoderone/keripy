# -*- encoding: utf-8 -*-
"""The disclosure rule: derived from the artifact, never a roster."""
from keri_serviceaid.providers.disclosure import (
    disclosable_bodies, is_openly_disclosable,
)


def _acdc(**over):
    sad = {"v": "ACDC10JSON000197_", "d": "E" + "A" * 43,
           "i": "E" + "I" * 43, "s": "E" + "S" * 43,
           "a": {"d": "E" + "D" * 43, "line_of_business": "auto"}}
    sad.update(over)
    return sad


def test_an_untargeted_public_acdc_is_disclosable():
    """The product mandate's shape: no `u`, no `a.i`. Its own schema says it
    exists to be found by anyone watching the CUO's KEL."""
    assert is_openly_disclosable(_acdc()) is True


def test_a_targeted_acdc_is_withheld():
    """`a.i` names the party it was issued to. Handing a role credential to
    whoever asks leaks it -- and openPolicy cannot tell the difference."""
    sad = _acdc()
    sad["a"]["i"] = "E" + "H" * 43
    assert is_openly_disclosable(sad) is False


def test_a_blinded_acdc_is_withheld():
    """A top-level `u` means the controller deliberately blinded it. ACDC
    v1.1: without `u` an ACDC SHOULD be considered public -- so WITH one it
    was made non-public on purpose, and disclosing on request defeats that."""
    assert is_openly_disclosable(_acdc(u="0A" + "B" * 22)) is False


def test_a_non_dict_is_never_disclosable():
    for bad in (None, "", 42, [], ("d",)):
        assert is_openly_disclosable(bad) is False, bad


def test_a_malformed_attribute_block_does_not_crash_the_rule():
    """`a` that is not a field map cannot name an Issuee, so it cannot be
    targeted -- but it must not raise either, or one bad row in the registry
    takes down every disclosure."""
    assert is_openly_disclosable(_acdc(a="not-a-dict")) is True


def test_disclosable_bodies_applies_the_rule_over_a_registry():
    public, targeted, blinded = _acdc(), _acdc(), _acdc()
    targeted["a"] = dict(targeted["a"], i="E" + "H" * 43)
    blinded["u"] = "0A" + "B" * 22

    class _S:
        def __init__(self, sad): self.sad = sad

    class _Creds:
        def getTopItemIter(self, keys):
            return [(("pub",), _S(public)), (("tgt",), _S(targeted)),
                    (("bld",), _S(blinded))]

    class _Reger:
        creds = _Creds()

    got = disclosable_bodies(_Reger())
    assert set(got) == {"pub"}, f"rule leaked: {set(got)}"
    assert got["pub"] is public


def test_an_unreadable_registry_discloses_nothing():
    """Fail CLOSED. The opposite direction from missing_bodies, deliberately:
    failing open here would hand out bodies on an error path."""
    class _Boom:
        def getTopItemIter(self, keys): raise RuntimeError("registry closed")

    class _Reger:
        creds = _Boom()

    assert disclosable_bodies(_Reger()) == {}
