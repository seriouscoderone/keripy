"""Exactly-once application of effects via an exn-SAID ledger on DynamoDB."""
from __future__ import annotations

import json
from typing import Any

from keri.db import subing

PROC_STORE = "proc."


class Ledger:
    """Records processed exn SAIDs + a small effect summary on a DynamoDBer.

    Call `record()` before sending the reply; a crash before `record()` leaves
    no entry so the next delivery retries cleanly (at-least-once delivery,
    exactly-once effects).
    """

    def __init__(self, db):
        # db is a DynamoDBer opened with PROC_STORE in its stores list.
        self.db = db
        self.proc = subing.Suber(db=db, subkey=PROC_STORE)

    def seen(self, said: str) -> Any | None:
        """Returns the recorded effect summary for `said`, or None if unseen.

        Callers store dicts by convention, so the value is normally a dict,
        but json.loads may return any JSON type.
        """
        raw = self.proc.get(keys=(said,))
        if raw is None:
            return None
        return json.loads(raw)

    def record(self, said: str, summary: dict) -> None:
        """Pins the effect summary for `said`, overwriting any prior entry.

        `summary` must be JSON-serializable (str/int/float/bool/None/dict/list
        values only); bytes or bytearray values raise TypeError at record time.
        The CESR grant itself is NOT stored here — duplicates get a JSON ack,
        not a replayed grant.
        """
        self.proc.pin(keys=(said,), val=json.dumps(summary).encode("utf-8"))
