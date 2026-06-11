"""Developer-facing contract: Service registry, Request, Reply, TestRuntime."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Request:
    """Verified, authorized inbound request handed to a developer function."""
    sender: str                       # verified caller AID prefix
    payload: dict                     # verified exn attributes (the `a` block)
    credentials: list = field(default_factory=list)  # verified attached ACDCs
    message_said: str = ""            # idempotency key (exn SAID)
    payload_said: str = ""            # SAID of the attributes block
    route: str = ""

    def now(self) -> str:
        from keri.help import helping
        return helping.nowIso8601()


@dataclass
class Reply:
    """Declarative reply. The framework performs issuance/signing/grant framing."""
    kind: str                         # "acdc" | "none" | "reject"
    recipient: Optional[str] = None
    attributes: Optional[dict] = None
    edges: Optional[dict] = None
    rules: Optional[dict] = None
    reason: Optional[str] = None

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


@dataclass
class Command:
    route: str
    issues: str            # ACDC schema SAID this command may issue
    fn: Callable[[Request], Reply]


class Service:
    """Registry populated by @service.command decorators at import time."""

    def __init__(self):
        self._commands: dict[str, Command] = {}
        self.schemas: list[dict] = []   # ACDC schema SADs to register at init

    def command(self, *, route: str, issues: str = ""):
        def deco(fn: Callable[[Request], Reply]):
            if route in self._commands:
                raise ValueError(f"duplicate route registered: {route}")
            self._commands[route] = Command(route=route, issues=issues, fn=fn)
            return fn
        return deco

    def register_schema(self, sad: dict) -> str:
        """Saidify an ACDC schema SAD, queue it for db registration, return its SAID.

        Called at developer-module import time so the runtime can load the schema
        into the Habery's schema store (required for credential issuance).
        """
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


# Module-level singleton imported by developer handler modules.
service = Service()


class TestRuntime:
    """In-memory runtime for unit-testing developer functions without keripy."""

    __test__ = False  # prevent pytest from collecting this class as a test suite

    def __init__(self, svc: Service):
        self.svc = svc

    def send(self, *, route: str, sender: str, payload: dict,
             credentials: list | None = None) -> Reply:
        cmd = self.svc.lookup(route)
        if cmd is None:
            raise KeyError(f"no command for route {route}")
        req = Request(sender=sender, payload=payload,
                      credentials=credentials or [],
                      message_said="EtestMsg", payload_said="EtestPay",
                      route=route)
        return cmd.fn(req)
