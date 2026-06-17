"""Authorizer Protocol + Allowlist default (Task 1 stub; Task 3 impl)."""
from __future__ import annotations


class Authorizer:  # Protocol promoted to a class in Task 3
    pass


class Allowlist:
    def __init__(self, aids=None):
        self.aids = list(aids or [])
