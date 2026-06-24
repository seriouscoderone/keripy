"""IdempotencyStore extension point + DynamoLedger default.

Records the SIGNED GRANT bytes keyed by the inbound exn SAID. `record` happens
AFTER issue but BEFORE deliver, so a delivery failure + client re-send hits
seen() and RE-DELIVERS the same grant (never re-issues) → exactly-once issuance,
at-least-once delivery. Stores raw CESR grant bytes (not a JSON summary) so the
replay path can re-deliver the identical message."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from keri.db import dbing, subing

PROC_STORE = "proc."


@runtime_checkable
class IdempotencyStore(Protocol):
    def seen(self, said: str) -> bytes | None:
        """Return the prior recorded grant for `said`, or None if unseen."""
        ...

    def record(self, said: str, grant: bytes) -> None:
        """Pin the grant for `said` (overwriting any prior entry)."""
        ...


class DynamoLedger:
    """Default idempotency store on a DynamoDBer opened with PROC_STORE."""

    def __init__(self, db):
        self.db = db
        self.proc = subing.Suber(db=db, subkey=PROC_STORE)

    def seen(self, said: str) -> bytes | None:
        raw = self.proc.get(keys=(said,))
        if raw is None:
            return None
        # Suber.get decodes the stored value to a str; CESR text is ASCII, so the
        # bytes->str->bytes round-trip is lossless. Re-encode to return bytes.
        return raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)

    def record(self, said: str, grant: bytes) -> None:
        self.proc.pin(keys=(said,), val=bytes(grant))


class LMDBLedger:
    """Idempotency store for the local runtime, on a dedicated keripy LMDBer.

    The idempotency ledger (inbound exn SAID -> signed grant) is a keri_serviceaid
    pipeline concept (cloud sibling: DynamoLedger), not a keripy primitive. It
    cannot live in the wallet's main Baser (hby.db): that LMDB env already uses all
    MaxNamedDBs=100 named sub-DBs (DbsFullError). So LMDBLedger opens its OWN sibling
    LMDBer (name '<db.name>-proc', alongside the wallet's db) and stores the ledger
    there via the same Suber that DynamoLedger uses.
    """

    def __init__(self, db):
        self._led = dbing.LMDBer(name=f"{db.name}-proc", base=db.base,
                                 headDirPath=db.headDirPath, temp=db.temp,
                                 reopen=True)
        self.proc = subing.Suber(db=self._led, subkey=PROC_STORE)

    def seen(self, said: str) -> bytes | None:
        raw = self.proc.get(keys=(said,))
        if raw is None:
            return None
        return raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)

    def record(self, said: str, grant: bytes) -> None:
        self.proc.pin(keys=(said,), val=bytes(grant))

    def close(self) -> None:
        self._led.close()
