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
from keri.core import serdering
from keri.vdr import credentialing

from keri_serviceaid.providers.issue import issue_credential, self_issue_and_grant


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


def test_issue_credential_honors_explicit_timestamp(issuer_hby, rating_schema, recipient_pre):
    """An explicit `timestamp=` must deterministically reach both the ACDC
    attribute block's `dt` and the TEL iss event's `dt` when the caller's
    attributes omit `dt` (retry/idempotency: same timestamp -> same SAID).

    Fidelity note: pre-refactor, `_issue_grant`'s timestamp only fed the
    `creder.attrib.get("dt", timestamp)` fallback, which is unreachable —
    `proving.credential` auto-stamps `dt` with its own now whenever absent —
    so an explicit timestamp silently never reached the TEL. The fix stamps
    it into the attribute block up front; this test pins that behavior.
    """
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-ts")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-ts", temp=True)

    ts = "2026-07-16T12:00:00.000000+00:00"
    credential_said = issue_credential(
        issuer_hby, hab, rgy,
        schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 7}, registry_name="svc-ts", timestamp=ts)

    # Credential attribute block carries the explicit timestamp as its dt.
    creder, prefixer, seqner, saider = rgy.reger.cloneCred(said=credential_said)
    assert creder.attrib["dt"] == ts

    # And the TEL iss event's dt is the same explicit timestamp.
    iss = rgy.reger.cloneTvtAt(credential_said)
    iserder = serdering.SerderKERI(raw=bytes(iss))
    assert iserder.ked["dt"] == ts


def test_issue_credential_empty_rules_omits_r_block(
        issuer_hby, rating_schema, recipient_pre):
    """Regression (live-demo blocker): issuing with an empty ``rules={}``
    must NOT emit an ACDC ``r`` block. The wallet issue dialog computes
    ``rules={}`` for a schema with no rules section; RATING_SCHEMA_SAD (like
    carrier_license) sets ``additionalProperties: false`` and declares no
    ``r`` property, so an empty ``r`` fails validation with "Additional
    properties are not allowed ('r' was unexpected)". ``issue_credential``
    must coerce ``rules or None`` (matching the legacy IssueCredentialDoer).
    Pre-fix this raised; post-fix it issues cleanly with no ``r`` field.
    """
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-empty-rules")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-empty-rules", temp=True)

    credential_said = issue_credential(
        issuer_hby, hab, rgy, schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 9}, registry_name="svc-empty-rules", rules={})

    creder, _p, _s, _sa = rgy.reger.cloneCred(said=credential_said)
    assert "r" not in creder.sad          # no empty rules block emitted


def test_self_issue_and_grant_threads_message_into_exn(
        issuer_hby, rating_schema, recipient_pre):
    """Regression (Task B9 fix): `self_issue_and_grant` must thread its
    `message` kwarg all the way through `frame_grant_for` into the framed
    IPEX grant exn's `a.m` field -- previously `frame_grant_for` hardcoded
    `message=""`, so this kwarg had nowhere to go even if a caller passed it.
    """
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-sig-message")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-sig-message", temp=True)

    credential_said, grant_said, raw = self_issue_and_grant(
        issuer_hby, hab, rgy, schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 5}, registry_name="svc-sig-message",
        message="hello reviewer", return_raw=True)

    serder = serdering.SerderKERI(raw=raw)
    assert serder.said == grant_said
    assert serder.sad["a"]["m"] == "hello reviewer"
