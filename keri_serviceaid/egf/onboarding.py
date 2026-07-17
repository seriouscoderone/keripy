"""Request-access orchestration: derive an onboarding "submit application"
plan from an EGF document, validate/shape a caller-supplied payload against
it, and select the authority the resulting grant is addressed to.

Pure module — no keystore/hby imports, no I/O beyond the injected `resolver`
(an `EgfResolver`-shaped object; only `resolve_micro_app` is used here). ACDC
construction (minting the credential, framing the IPEX grant) is deliberately
NOT this module's concern — see `keri_serviceaid/providers/issue.py`'s
`issue_credential` / `frame_grant_for` / `self_issue_and_grant`, which consume
the `RequestPlan.attributes`/`registry_name` this module produces.

Design notes (acdc-design skill reconciliation — see task-6-report.md for the
full write-up):

- `registry_name = application_credential.schema_said` follows Locksmith's
  established registry-name == schema-SAID convention (one registry per
  credential TYPE, keyed by its schema SAID) — a `lifecycle-and-registries.md`
  concern, not re-litigated here.
- `select_authority`'s `accept_phases` parameter is the direct runtime hook
  for the "open now, closed later" authority-bootstrapping pattern
  (`governance-and-bootstrapping.md`): a caller in the ecosystem's bootstrap
  window passes `("bootstrap", "production")` to accept a placeholder
  authority; once the real authority (e.g. a state DOI) is KERI-native and
  the EGF's `authorities` entries are updated to `phase="production"`, a
  caller passes only `("production",)` and placeholder authorities silently
  stop matching — no schema change, no code change, per the bootstrap
  transition plan.
- The application credential this module's payload feeds is `self_issued`
  per the EGF's `credentials` entry (the requester attests about themself);
  the grant credential it is chained from (`chained_from`) is issued back by
  the selected `Authority` on approval. This module only derives WHAT to
  submit and TO WHOM — it holds no opinion on edge operators or disclosure
  mode; those are schema-authoring decisions already baked into the EGF's
  pinned schema SAIDs.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Iterable, Optional

from jsonschema import Draft202012Validator

from keri_serviceaid.egf.documents import Authority, CredentialEntry, EgfDocument
from keri_serviceaid.egf.errors import EgfDocumentError, NoAuthorityError


@dataclass(frozen=True)
class RequestPlan:
    role_id: str
    grant_credential: CredentialEntry
    application_credential: CredentialEntry
    micro_app_said: str
    command_id: str
    payload_schema: dict
    schema_saids_to_seed: tuple
    registry_name: str


def derive_request(resolver, egf: EgfDocument, role_id: str) -> RequestPlan:
    """Walk `egf` from `role_id` to the payload schema for its onboarding
    "submit application" command, resolving the role's micro-app via
    `resolver` (an `EgfResolver`-shaped object exposing `resolve_micro_app`).

    Raises `EgfDocumentError` if the role has no onboarding block, or if the
    resolved micro-app has no command matching `request_command_id`.
    """
    role = egf.role(role_id)
    if role.onboarding is None:
        raise EgfDocumentError(f"role {role_id!r} has no onboarding block in EGF document {egf.said}")

    onboarding = role.onboarding
    grant_credential = egf.credential(onboarding.grant_credential_id)
    if grant_credential.chained_from is None:
        raise EgfDocumentError(
            f"grant credential {grant_credential.id!r} for role {role_id!r} has no "
            "chained_from application credential"
        )
    application_credential = egf.credential(grant_credential.chained_from)

    micro_app_said = onboarding.request_micro_app_said
    micro_app = resolver.resolve_micro_app(micro_app_said)
    command_id = onboarding.request_command_id
    command = next(
        (c for c in micro_app.get("commands", []) if c.get("id") == command_id), None
    )
    if command is None:
        raise EgfDocumentError(
            f"micro-app {micro_app_said!r} has no command {command_id!r} "
            f"(role {role_id!r})"
        )
    payload_schema = command["payload_schema"]

    return RequestPlan(
        role_id=role_id,
        grant_credential=grant_credential,
        application_credential=application_credential,
        micro_app_said=micro_app_said,
        command_id=command_id,
        payload_schema=payload_schema,
        schema_saids_to_seed=(grant_credential.schema_said, application_credential.schema_said),
        registry_name=application_credential.schema_said,
    )


def validate_payload(plan: RequestPlan, payload: dict) -> None:
    """Validate `payload` against `plan.payload_schema` (JSON-Schema draft
    2020-12). Raises `EgfDocumentError` with the offending field path folded
    into the message on the first violation (fail closed)."""
    validator = Draft202012Validator(plan.payload_schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        path = first.json_path
        raise EgfDocumentError(
            f"payload validation failed for command {plan.command_id!r} at {path!r}: {first.message}"
        )


def build_attributes(plan: RequestPlan, payload: dict, now_iso: Optional[str] = None) -> dict:
    """Return the ACDC attribute block for `plan`'s application credential:
    `payload` as given, plus a `submitted_at` autofill (`now_iso` if given,
    else UTC-now) iff the payload omits it AND the schema requires it."""
    attrs = dict(payload)
    required = plan.payload_schema.get("required", [])
    if "submitted_at" in required and "submitted_at" not in attrs:
        attrs["submitted_at"] = now_iso or datetime.datetime.now(datetime.timezone.utc).isoformat()
    return attrs


def select_authority(egf: EgfDocument, plan: RequestPlan, context: dict,
                     accept_phases: Iterable[str]) -> Authority:
    """Select the single `Authority` (from `egf`) that issues `plan`'s grant
    credential, matching `context` within `accept_phases`.

    Raises `NoAuthorityError` — listing the contexts actually on offer for
    this role within `accept_phases` — when zero or more than one authority
    matches (an ambiguous match is as unusable to the caller as none)."""
    accept_phases = tuple(accept_phases)
    role_id = plan.grant_credential.issuer_role
    matches = egf.authorities(role_id, context=context, accept_phases=accept_phases)
    if len(matches) == 1:
        return matches[0]

    offered = egf.authorities(role_id, context=None, accept_phases=accept_phases)
    offered_contexts = [a.context for a in offered]
    if not matches:
        raise NoAuthorityError(
            f"no authority for role {role_id!r} matches context {context} "
            f"within phases {accept_phases}; offered contexts: {offered_contexts}"
        )
    raise NoAuthorityError(
        f"ambiguous authority for role {role_id!r} matching context {context} "
        f"within phases {accept_phases}: {[a.display_name for a in matches]}; "
        f"offered contexts: {offered_contexts}"
    )
