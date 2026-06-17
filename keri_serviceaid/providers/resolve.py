"""Resolver Protocol + OracleResolver default (Task 1 stub; Task 3 impl)."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Endpoint:
    role: str = ""
    eid: str = ""
    url: str = ""


class Resolver:  # Protocol promoted to a class in Task 3
    pass


class OracleResolver:
    def __init__(self, fallback=None):
        self.fallback = fallback
