"""Authorization policy, evaluated after KERI verification."""
from __future__ import annotations

from dataclasses import dataclass, field

from .contract import Request


@dataclass
class Policy:
    allowlist: list[str] = field(default_factory=list)  # empty ⇒ any sender
    required_schema: str = ""                            # empty ⇒ none required


def authorize(req: Request, policy: Policy) -> tuple[bool, str]:
    """Return (allowed, reason). reason is empty when allowed."""
    if policy.allowlist and req.sender not in policy.allowlist:
        return False, f"sender {req.sender} not in allowlist"
    if policy.required_schema:
        present = any(isinstance(c, dict) and c.get("schema") == policy.required_schema
                      for c in req.credentials)
        if not present:
            return False, f"missing required credential of schema {policy.required_schema}"
    return True, ""
