"""Typed, frozen-dataclass view over a verified egf-doc/0.1 SAD.

`EgfDocument.from_sad` is the only construction path: it validates the SAD against the
packaged egf-doc/0.1 meta-schema (fail closed — any violation, including an unsupported
`spec_version`, raises `EgfDocumentError`) and maps its sections into the dataclasses below.
Callers are expected to have already run `verify_sad` (Task 2) to establish SAID integrity;
this module is concerned with *shape*, not provenance.
"""
import importlib.resources
import json
from dataclasses import dataclass, field
from typing import Iterable, Optional

import jsonschema

from keri_serviceaid.egf.errors import EgfDocumentError

SUPPORTED_SPEC_VERSION = "egf-doc/0.1"


@dataclass(frozen=True)
class Onboarding:
    grant_credential_id: str
    request_micro_app_said: str = ""
    request_command_id: str = ""

    @property
    def apply_mode(self) -> bool:
        """True when the role onboards via a bare IPEX apply (no form/micro-app).
        Present request_micro_app_said => form mode (the carrier pattern)."""
        return not self.request_micro_app_said


@dataclass(frozen=True)
class Role:
    id: str
    display_name: str
    kind: str
    description: str
    onboarding: Optional[Onboarding]


@dataclass(frozen=True)
class CredentialEntry:
    id: str
    name: str
    schema_said: str
    issuer_role: str
    holder_role: Optional[str]
    disclosure_mode: str
    chained_from: Optional[str]
    self_issued: bool


@dataclass(frozen=True)
class Endpoint:
    """One reachability entry on an authority. `mode` is the runtime
    dispatch discriminator (today: "direct" — direct-mode peer TCP);
    unknown modes are carried, not rejected (forward-compatible)."""

    mode: str
    scheme: str = ""
    oobi_ref: str = ""


@dataclass(frozen=True)
class Authority:
    role_id: str
    display_name: str
    aid: str
    phase: str
    context: dict
    credentials: tuple
    endpoints: "tuple[Endpoint, ...]"
    phase_note: str
    expected_transition: Optional[str]


@dataclass(frozen=True)
class ContextDimension:
    id: str
    applies_to_roles: tuple
    kind: str
    values_ref: str
    prompt: str
    binds: str


@dataclass(frozen=True)
class MicroAppRef:
    said: str
    id: str
    role_id: str


def _load_meta_schema() -> dict:
    ref = importlib.resources.files("keri_serviceaid.egf") / "schemas" / "egf-doc-0.1.json"
    return json.loads(ref.read_text())


def _onboarding_from_sad(sad: Optional[dict]) -> Optional[Onboarding]:
    if sad is None:
        return None
    return Onboarding(
        grant_credential_id=sad["grant_credential_id"],
        request_micro_app_said=sad.get("request_micro_app_said", ""),
        request_command_id=sad.get("request_command_id", ""),
    )


def _role_from_sad(sad: dict) -> Role:
    return Role(
        id=sad["id"],
        display_name=sad["display_name"],
        kind=sad["kind"],
        description=sad["description"],
        onboarding=_onboarding_from_sad(sad.get("onboarding")),
    )


def _credential_from_sad(sad: dict) -> CredentialEntry:
    return CredentialEntry(
        id=sad["id"],
        name=sad["name"],
        schema_said=sad["schema_said"],
        issuer_role=sad["issuer_role"],
        holder_role=sad.get("holder_role"),
        disclosure_mode=sad["disclosure_mode"],
        chained_from=sad["chained_from"],
        self_issued=sad["self_issued"],
    )


def _authority_from_sad(sad: dict) -> Authority:
    return Authority(
        role_id=sad["role_id"],
        display_name=sad["display_name"],
        aid=sad["aid"],
        phase=sad["phase"],
        context=dict(sad["context"]),
        credentials=tuple(sad["credentials"]),
        endpoints=tuple(
            Endpoint(
                mode=e["mode"],
                scheme=e.get("scheme", ""),
                oobi_ref=e.get("oobi_ref", ""),
            )
            for e in sad["endpoints"]
        ),
        phase_note=sad["phase_note"],
        expected_transition=sad["expected_transition"],
    )


def _context_dimension_from_sad(sad: dict) -> ContextDimension:
    return ContextDimension(
        id=sad["id"],
        applies_to_roles=tuple(sad["applies_to_roles"]),
        kind=sad["kind"],
        values_ref=sad["values_ref"],
        prompt=sad["prompt"],
        binds=sad["binds"],
    )


def _micro_app_from_sad(sad: dict) -> MicroAppRef:
    return MicroAppRef(said=sad["said"], id=sad["id"], role_id=sad["role_id"])


@dataclass(frozen=True)
class EgfDocument:
    said: str
    _roles: tuple = field(repr=False)
    _credentials: tuple = field(repr=False)
    _authorities: tuple = field(repr=False)
    _micro_apps: tuple = field(repr=False)
    _context_dimensions: tuple = field(repr=False)
    accepted_schema_saids: tuple

    @classmethod
    def from_sad(cls, sad: dict) -> "EgfDocument":
        meta = _load_meta_schema()
        try:
            jsonschema.validate(instance=sad, schema=meta)
        except jsonschema.exceptions.ValidationError as ex:
            raise EgfDocumentError(f"egf-doc/0.1 meta-schema violation: {ex.message}") from ex

        # Belt-and-suspenders: unreachable while the schema pins spec_version via `const`
        # (the jsonschema.validate above already rejects any other value). Kept for when
        # the schema loosens this to an enum of supported versions.
        if sad.get("spec_version") != SUPPORTED_SPEC_VERSION:
            raise EgfDocumentError(
                f"unsupported spec_version {sad.get('spec_version')!r}; "
                f"this build supports {SUPPORTED_SPEC_VERSION!r}"
            )

        return cls(
            said=sad["d"],
            _roles=tuple(_role_from_sad(r) for r in sad["roles"]),
            _credentials=tuple(_credential_from_sad(c) for c in sad["credentials"]),
            _authorities=tuple(_authority_from_sad(a) for a in sad["authorities"]),
            _micro_apps=tuple(_micro_app_from_sad(m) for m in sad["micro_apps"]),
            _context_dimensions=tuple(
                _context_dimension_from_sad(cd) for cd in sad["context_dimensions"]
            ),
            accepted_schema_saids=tuple(sad["accepted_schema_saids"]),
        )

    def personas(self) -> list:
        """Roles WITH an onboarding block — the persona picker's source list."""
        return [r for r in self._roles if r.onboarding is not None]

    def micro_apps(self) -> list:
        """Every micro-app reference, in document order.

        `micro_app_for_role` answers "the app for this role"; this answers "every app
        this ecosystem declares", which is what a walk keyed by an arriving
        credential's schema needs — it does not know the role yet, that is what it is
        trying to find out.
        """
        return list(self._micro_apps)

    def credentials_for_holder(self, role_id: str) -> list:
        """Catalog entries whose holder is `role_id` — empty when none.

        Empty rather than raising: "this role holds no role credential" is a real
        ecosystem answer a caller must inspect and reject on its own terms.
        """
        return [c for c in self._credentials if c.holder_role == role_id]

    def role(self, role_id: str) -> Role:
        for r in self._roles:
            if r.id == role_id:
                return r
        raise EgfDocumentError(f"no role {role_id!r} in EGF document {self.said}")

    def credential(self, cred_id: str) -> CredentialEntry:
        for c in self._credentials:
            if c.id == cred_id:
                return c
        raise EgfDocumentError(f"no credential {cred_id!r} in EGF document {self.said}")

    def credential_by_schema(self, schema_said: str) -> Optional[CredentialEntry]:
        """The catalog entry whose ACDC schema SAID matches, or None —
        for consumers keyed by what arrived on the wire (an embedded
        ACDC's `s` field) rather than by catalog id."""
        for c in self._credentials:
            if c.schema_said == schema_said:
                return c
        return None

    def micro_app_for_role(self, role_id: str) -> MicroAppRef:
        for m in self._micro_apps:
            if m.role_id == role_id:
                return m
        raise EgfDocumentError(f"no micro_app for role {role_id!r} in EGF document {self.said}")

    def context_dimensions(self, role_id: str) -> list:
        return [cd for cd in self._context_dimensions if role_id in cd.applies_to_roles]

    def authorities(
        self,
        role_id: str,
        context: Optional[dict] = None,
        accept_phases: Iterable[str] = ("production",),
    ) -> list:
        accept_phases = tuple(accept_phases)
        out = []
        for a in self._authorities:
            if a.role_id != role_id:
                continue
            if a.phase not in accept_phases:
                continue
            if context is not None and any(a.context.get(k) != v for k, v in context.items()):
                continue
            out.append(a)
        return out

    def all_authorities(self, accept_phases: Iterable[str] = ("production",)) -> list:
        """Every authority entry in an accepted phase, across ALL roles —
        for consumers that need the full trust registry (e.g. transport
        bring-up pairing every direct-mode authority), not one role's slice."""
        accept_phases = tuple(accept_phases)
        return [a for a in self._authorities if a.phase in accept_phases]
