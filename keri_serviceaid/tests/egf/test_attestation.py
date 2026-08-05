"""The verification library. One implementation, two consumers: the concierge CLI
and (Plan C2) the HOA. Amendment C §14.2."""
import json

import pytest
from keri.core import coring

from keri_serviceaid.egf.attestation import AttestationVerdict, verify_attestation

SCHEMA = "ESchemaSAIDSchemaSAIDSchemaSAIDSchemaSAIDSc"
ACDC = {"v": "ACDC10JSON0001ae_", "d": "ECredSAIDCredSAIDCredSAIDCredSAIDCredSAIDC",
        "i": "ECuoAidCuoAidCuoAidCuoAidCuoAidCuoAidCuoAid",
        "ri": "ERegistrySAIDRegistrySAIDRegistrySAIDRegist",
        "s": SCHEMA, "a": {"d": "EAttr", "line_of_business": "auto"}}


def _saidified(doc: dict, label: str = "d") -> "tuple[str, bytes]":
    """Same helper as test_verify.py::_saidified — build a manifest whose `said`
    genuinely re-derives from `raw` via the real SAID algorithm, not a fixture where
    both were hand-typed and merely happen to look plausible."""
    doc = dict(doc); doc[label] = ""
    saider, sad = coring.Saider.saidify(sad=doc, label=label)
    return sad[label], json.dumps(sad).encode()


class _Egf:
    """Minimal stand-in for EgfDocument's read surface. Only the two attributes this
    module touches, so a shape change in EgfDocument surfaces here as an AttributeError
    rather than being silently absorbed."""

    def __init__(self, accepted=(SCHEMA,)):
        self.accepted_schema_saids = tuple(accepted)


def test_a_conforming_attestation_verifies_and_names_every_check_it_ran():
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued")
    assert v.ok is True
    assert v.failures == ()
    assert set(v.checks) == {"schema_accepted", "tel_state_current"}


def test_a_schema_outside_the_EGF_fails_and_says_which_check():
    v = verify_attestation(acdc=ACDC, egf=_Egf(accepted=("EOther",)), tel_state="issued")
    assert v.ok is False
    assert v.checks["schema_accepted"] is False
    assert any("accepted_schema_saids" in f for f in v.failures)


def test_a_revoked_credential_fails_even_with_an_accepted_schema():
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="revoked")
    assert v.ok is False
    assert v.checks["tel_state_current"] is False


def test_an_UNKNOWN_tel_state_fails_closed_rather_than_being_treated_as_current():
    """The state a reader gets when the TEL could not be read at all. Treating it as
    issued would make an unreachable registry indistinguishable from a good one."""
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state=None)
    assert v.ok is False
    assert v.checks["tel_state_current"] is False


def test_the_manifest_check_runs_only_when_a_manifest_is_supplied():
    """Not every attestation carries one — only the actuary's rate program does — so
    an absent manifest must not silently count as a passed check."""
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued")
    assert "manifest_rederives" not in v.checks


def test_a_manifest_whose_bytes_do_not_rederive_fails():
    manifest = {"raw": b'{"d":"EWrong","files":[]}', "said": "ENotTheSaidOfThoseBytes"}
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued", manifest=manifest)
    assert v.ok is False
    assert v.checks["manifest_rederives"] is False


def test_a_manifest_with_unparseable_bytes_fails_closed_rather_than_raising():
    """verify_sad raises EgfDocumentError (not EgfIntegrityError) for JSON that does not
    even parse — a distinct exception type from a SAID mismatch. The manifest check must
    fail closed on that too, not let it escape verify_attestation as an uncaught error."""
    manifest = {"raw": b"{not json", "said": "EAnySaidHereDoesNotMatterForThisCase00000000"}
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued", manifest=manifest)
    assert v.ok is False
    assert v.checks["manifest_rederives"] is False


def test_a_manifest_missing_a_required_key_fails_closed_rather_than_raising():
    """A malformed manifest dict (e.g. no 'said') is a caller bug, but this is a
    fail-closed verification boundary — it must report False, not raise KeyError."""
    manifest = {"raw": b'{"d":"EWrong","files":[]}'}
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued", manifest=manifest)
    assert v.ok is False
    assert v.checks["manifest_rederives"] is False


def test_a_manifest_that_genuinely_rederives_passes_and_the_verdict_is_ok():
    """The success path. Every other manifest test above is a failure case; without
    this one, hardcoding the success branch to False would still leave the whole
    suite green — 'we checked and it failed' would be the only value ever proven."""
    said, raw = _saidified({"d": "", "files": ["rate_table.csv"]})
    manifest = {"raw": raw, "said": said}
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued", manifest=manifest)
    assert v.checks["manifest_rederives"] is True
    assert v.ok is True
    assert v.failures == ()


def test_a_verdict_is_immutable_so_a_caller_cannot_flip_ok_after_the_fact():
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued")
    with pytest.raises((AttributeError, TypeError)):
        v.ok = False


def test_the_admin_rooting_check_runs_when_the_caller_supplies_the_answer():
    """The library does not query the vault — the caller does the credgate lookup and
    passes the boolean. That keeps this module pure and testable, matching
    egf/onboarding.py's posture of taking an injected resolver and no hby."""
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued", issuer_holds=True)
    assert v.ok is True
    assert v.checks["issuer_admin_rooted"] is True


def test_an_issuer_holding_no_role_credential_fails():
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued", issuer_holds=False)
    assert v.ok is False
    assert v.checks["issuer_admin_rooted"] is False


def test_omitting_the_admin_rooting_answer_leaves_the_check_ABSENT_not_passing():
    """§8's whole point: nothing stops an arbitrary AID from anchoring a
    mandate-shaped attestation. A caller that forgot to ask must not get a verdict
    that looks like it checked."""
    v = verify_attestation(acdc=ACDC, egf=_Egf(), tel_state="issued")
    assert "issuer_admin_rooted" not in v.checks
