"""IdempotencyStore Protocol + DynamoLedger default (Task 1 stub; Task 3 impl)."""
from __future__ import annotations


class IdempotencyStore:  # Protocol promoted to a class in Task 3
    pass


class DynamoLedger:
    def __init__(self, db):
        self.db = db
