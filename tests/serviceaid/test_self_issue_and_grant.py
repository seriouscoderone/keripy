"""Live-stack test for `self_issue_and_grant` (Task 6, §A6).

Reuses this directory's established real-Habery fixture patterns
(`issuer_hby`, `rating_schema`, `recipient_pre` from conftest.py — see
`test_providers_issue.py`/`test_schema_host_handler_e2e.py` for the same
shape) rather than `keri_serviceaid/tests/egf/`, which has no v1-hold
conftest and mints v2 habs that fail v1 TEL issuance (the 3 accepted
baseline failures live there). This tree's autouse `_hold_serviceaid_v1`
fixture is exactly what a real-Habery issuance test needs.

Exercises the whole self-issue-and-grant path against a real two-Habery
stack: mint + anchor + issue a credential into a real registry, frame its
IPEX grant, and confirm the Task-5 `ProgressSink` emission actually fires on
a real issuance (closing the verification gap the brief calls out — the unit
tests in test_progress.py only check the sink *plumbing*, not that a real
issuance *emits*).
"""
from keri.vdr import credentialing

from keri_serviceaid.providers.issue import self_issue_and_grant


class Capture:
    def __init__(self):
        self.events = []

    def on_event(self, source, event_type, data):
        self.events.append((source, event_type, data))


def test_self_issue_and_grant_against_real_stack(issuer_hby, rating_schema, recipient_pre):
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc", temp=True)

    sink = Capture()
    credential_said, grant_said = self_issue_and_grant(
        issuer_hby, hab, rgy,
        schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 42}, registry_name="svc", sink=sink)

    # Both returned identifiers are non-empty SAID strings.
    assert isinstance(credential_said, str) and len(credential_said) > 0
    assert isinstance(grant_said, str) and len(grant_said) > 0
    assert credential_said != grant_said

    # The credential actually landed in the issuer's registry (reger).
    creder, prefixer, seqner, saider = rgy.reger.cloneCred(said=credential_said)
    assert creder.said == credential_said
    assert creder.schema == schema_said
    assert creder.attrib["i"] == recipient_pre

    # The Task-5 sink emission fires on a REAL issuance (not just the
    # plumbing check in test_progress.py::test_issuer_accepts_and_uses_sink).
    assert ("IssueCredentialDoer", "credential_issued",
           {"said": credential_said, "schema_said": schema_said}) in sink.events
