"""Authorizer extension point + Allowlist default.

Evaluated AFTER KERI verification. v1 default is sender-AID gating (Allowlist);
the credential-presentation gate (CredentialGate(required_schema=…)) is the
named crown-jewel follow-on and is NOT implemented here."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..contract import Request


@runtime_checkable
class Authorizer(Protocol):
    def authorize(self, req: Request) -> tuple[bool, str]:
        """Return (allow, reason); reason is "" when allowed."""
        ...


class Allowlist:
    """Default authorizer: an explicit set of permitted sender AIDs.

    An empty allowlist means any verified sender is allowed. v1 never inspects
    req.credentials (always [] under this authz); credential gating is a follow-on.
    """

    def __init__(self, aids: list[str] | None = None):
        self.aids = list(aids or [])

    def authorize(self, req: Request) -> tuple[bool, str]:
        if self.aids and req.sender not in self.aids:
            return False, f"sender {req.sender} not in allowlist"
        return True, ""
