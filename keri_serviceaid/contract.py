"""Developer contract (Task 1 stub; fully implemented in Task 2)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Request:
    sender: str
    route: str
    payload: dict
    credentials: list = field(default_factory=list)
    message_said: str = ""
    key_state: object = None


@dataclass
class Reply:
    kind: str
    recipient: Optional[str] = None
    attributes: Optional[dict] = None
    edges: Optional[dict] = None
    rules: Optional[dict] = None
    reason: Optional[str] = None


@dataclass
class Command:
    route: str
    payload_schema: Optional[dict]
    issues: str
    fn: Callable[[Request], Reply]


class ServiceAid:
    pass


class TestRuntime:
    __test__ = False
