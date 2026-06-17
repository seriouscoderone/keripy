"""Verifier Protocol + OracleVerifier default (Task 1 stub; Task 3 impl)."""
from __future__ import annotations
from dataclasses import dataclass


class VerificationError(Exception):
    pass


@dataclass
class KeyState:
    pre: str = ""
    tier: str = "receipts"


class Verifier:  # Protocol promoted to a class in Task 3
    pass


class OracleVerifier:
    def __init__(self, tier: str = "receipts"):
        self.tier = tier
