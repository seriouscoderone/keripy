"""Reachability-artifact retrieval by AID.

An OOBI artifact is a CESR reply stream (KEL + signed /loc/scheme +
signed /end/role) for one AID. Unlike EGF documents (SAID-verified
bytes), an OOBI's authenticity is established by KERI verification at
PARSE time in the consumer's vault (signatures against key state, BADA)
— a source is trusted for availability only, never integrity.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from keri_serviceaid.egf.errors import OobiNotFound

_AID_RE = re.compile(r"^[A-Za-z0-9_-]{44}$")


class OobiSource(Protocol):
    def fetch(self, aid: str) -> bytes: ...


class LocalDirOobiSource:
    """Reads `<root>/oobis/<aid>.cesr` — the bundled-local mirror of a
    future `/oobi/<aid>` registry endpoint (same doctrine as
    LocalDirSource for EGF documents)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def fetch(self, aid: str) -> bytes:
        if not _AID_RE.match(aid):
            raise OobiNotFound(aid, str(self.root))
        path = self.root / "oobis" / f"{aid}.cesr"
        if not path.is_file():
            raise OobiNotFound(aid, str(path))
        return path.read_bytes()


class HttpOobiEndpointSource:
    """GET {base_url}/oobi/{aid} — standard KERI OOBI IURL convention.
    Stub only — remote registry resolution is a declared seam; fails
    closed rather than silently degrading."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def fetch(self, aid: str) -> bytes:
        raise NotImplementedError(
            "HTTP OOBI resolution is a declared seam (remote registry)")
