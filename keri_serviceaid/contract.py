"""Developer-facing contract: ServiceAid registry, Request, Reply, Command,
TestRuntime. No keripy import at module top (register_schema imports lazily) so
this stays cheap to import in the dev's compute_code module."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Request:
    """Verified, authorized inbound request handed to a developer function."""
    sender: str                       # verified caller AID prefix
    route: str                        # the signed exn `r`
    payload: dict                     # verified exn attributes (the `a` block)
    credentials: list = field(default_factory=list)  # verified presented ACDCs ([] under Allowlist)
    message_said: str = ""            # exn SAID — the idempotency key
    key_state: object = None          # resolved sender KeyState (assurance tier)

    def now(self) -> str:
        """Convenience: current RFC-3339/iso8601 timestamp, so a developer
        command function can stamp `dt` on reply attributes without importing
        keripy itself. Lazy-imports keripy (keeps module import cheap)."""
        from keri.help import helping
        return helping.nowIso8601()


@dataclass
class Reply:
    """Declarative reply. The framework performs issuance/signing/grant framing."""
    kind: str                         # "acdc" | "none" | "reject" | "revoke" | "publish"
    recipient: Optional[str] = None
    attributes: Optional[dict] = None
    edges: Optional[dict] = None
    rules: Optional[dict] = None
    reason: Optional[str] = None
    schema_said: Optional[str] = None
    artifact_said: Optional[str] = None
    artifact_bytes: Optional[bytes] = None
    want_receipt: bool = False

    @classmethod
    def acdc(cls, *, recipient: str, attributes: dict,
             edges: dict | None = None, rules: dict | None = None) -> "Reply":
        return cls(kind="acdc", recipient=recipient, attributes=attributes,
                   edges=edges, rules=rules)

    @classmethod
    def none(cls) -> "Reply":
        return cls(kind="none")

    @classmethod
    def reject(cls, *, reason: str) -> "Reply":
        return cls(kind="reject", reason=reason)

    @classmethod
    def revoke(cls, *, recipient: str, credential_said: str,
               reason: str = "") -> "Reply":
        return cls(kind="revoke", recipient=recipient,
                   attributes={"credential_said": credential_said}, reason=reason)

    @classmethod
    def publish(cls, *, recipient: str, artifact_said: str, artifact_bytes: bytes,
                attributes: dict, want_receipt: bool = False,
                edges: dict | None = None, rules: dict | None = None) -> "Reply":
        """Store a public SAD artifact (by SAID) and record its publication.
        The framework runs the ArtifactStore effect, then issues an optional
        `publication_receipt` ACDC (delivered iff `want_receipt`)."""
        return cls(kind="publish", recipient=recipient, attributes=attributes,
                   edges=edges, rules=rules, artifact_said=artifact_said,
                   artifact_bytes=artifact_bytes, want_receipt=want_receipt)


@dataclass(frozen=True)
class CredentialReq:
    """Per-command inbound credential requirement, enforced by CredentialGate.

    `schema` is the required ACDC schema SAID the caller must hold (as issuee).
    `issuer`, when set, additionally constrains who issued it. `presentation`
    and `cadence` are the declared ceremony policy (v1 default: present once via
    IPEX grant, cache, re-check TEL revocation per request)."""
    schema: str
    issuer: Optional[str] = None
    presentation: str = "cache"          # "cache" | "embed" | "thread"
    cadence: str = "revocation-recheck"


@dataclass
class Command:
    """Route → handler binding. `payload_schema` is an optional JSON-Schema for
    the `a` block; it is YAGNI in v1 (stored but not enforced) and promotable later."""
    route: str
    payload_schema: Optional[dict]
    issues: str                       # ACDC schema SAID this command may issue
    fn: Callable[[Request], Reply]
    requires_credential: Optional[CredentialReq] = None


class ServiceAid:
    """The declared entity: identity config + injected providers + command
    registry. The dev names it (`svc = ServiceAid(...)`); the framework finds it
    via the `module:attr` entry ref. Providers left None get their default wired
    in the runtime (here we just store None)."""

    def __init__(self, *, alias: str, witnesses: list[str] | None = None,
                 toad: int = 0, authz=None, verifier=None, resolver=None,
                 issuer=None, deliverer=None, idempotency=None,
                 artifact_store=None):
        self.alias = alias
        self.witnesses = witnesses or []
        self.toad = toad
        self.authz = authz
        self.verifier = verifier
        self.resolver = resolver
        self.issuer = issuer
        self.deliverer = deliverer
        self.idempotency = idempotency
        self.artifact_store = artifact_store
        self._commands: dict[str, Command] = {}
        self.schemas: list[dict] = []   # ACDC schema SADs to register at init

    def command(self, *, route: str, issues: str = "",
                payload_schema: dict | None = None,
                requires_credential: Optional["CredentialReq"] = None):
        if route.startswith("/ipex/"):
            raise ValueError(f"route {route!r} is reserved: /ipex/* is owned by "
                             "the IPEX protocol and may not be a command route")

        def deco(fn: Callable[[Request], Reply]):
            if route in self._commands:
                raise ValueError(f"duplicate route registered: {route}")
            self._commands[route] = Command(route=route, payload_schema=payload_schema,
                                             issues=issues, fn=fn,
                                             requires_credential=requires_credential)
            return fn
        return deco

    def register_schema(self, sad: dict) -> str:
        """Saidify an ACDC schema SAD, queue it for db registration, return its SAID."""
        from keri.core import scheming
        from keri.kering import Kinds
        schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
        self.schemas.append(dict(schemer.sed))
        return schemer.said

    def lookup(self, route: str) -> Optional[Command]:
        return self._commands.get(route)

    @property
    def routes(self) -> list[str]:
        return list(self._commands)


class TestRuntime:
    """In-memory runtime for unit-testing developer command functions without keripy."""

    __test__ = False  # do not collect as a pytest suite

    def __init__(self, svc: ServiceAid):
        self.svc = svc

    def send(self, *, route: str, sender: str, payload: dict,
             credentials: list | None = None) -> Reply:
        cmd = self.svc.lookup(route)
        if cmd is None:
            raise KeyError(f"no command for route {route}")
        req = Request(sender=sender, route=route, payload=payload,
                      credentials=credentials or [], message_said="EtestMsg")
        return cmd.fn(req)
