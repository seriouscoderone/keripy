"""Exactly-once application of effects via an exn-SAID ledger on DynamoDB."""
from __future__ import annotations

import json

from keri.db import subing

PROC_STORE = "proc."


class Ledger:
    """Records processed exn SAIDs + a small effect summary on a DynamoDBer."""

    def __init__(self, db):
        # db is a DynamoDBer opened with PROC_STORE in its stores list.
        self.db = db
        self.proc = subing.Suber(db=db, subkey=PROC_STORE)

    def seen(self, said: str) -> dict | None:
        raw = self.proc.get(keys=(said,))
        if raw is None:
            return None
        return json.loads(raw)

    def record(self, said: str, summary: dict) -> None:
        self.proc.pin(keys=(said,), val=json.dumps(summary).encode("utf-8"))
