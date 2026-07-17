"""Content-addressed sources for EGF artifacts.

A source is trusted for AVAILABILITY only, never integrity — every byte it returns
is re-verified against its claimed SAID by the resolver (resolver.py). A source
implementation's only job is "hand me the bytes for this SAID, or tell me you
don't have them"; it must never itself decide whether the content is trustworthy.
"""
import re
from pathlib import Path
from typing import Protocol

from keri_serviceaid.egf.errors import EgfNotFound

_SAID_RE = re.compile(r"[A-Za-z0-9_-]{44}")


class EgfSource(Protocol):
    """Structural contract: anything with a matching `fetch` works as a source."""

    def fetch(self, said: str) -> bytes:
        """Return the raw bytes claimed to SAID `said`, or raise `EgfNotFound`."""
        ...


class LocalDirSource:
    """Reads content-addressed EGF artifacts from a local directory: `<root>/<said>.json`.

    Used for local development, tests, and offline/air-gapped operation.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def fetch(self, said: str) -> bytes:
        if not _SAID_RE.fullmatch(said):
            # Malformed identifiers fail closed as not-found; no path is ever
            # constructed from unvalidated input (path-traversal / absolute-path
            # guard).
            raise EgfNotFound(said, str(self.root))
        path = self.root / f"{said}.json"
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise EgfNotFound(said, str(self.root)) from None


class HttpOobiSource:
    """HTTP/OOBI-based EGF resolution.

    Stub only — the transport project (#2) implements the actual OOBI discovery
    and HTTP fetch. Every method fails closed rather than silently degrading.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url

    def fetch(self, said: str) -> bytes:
        raise NotImplementedError(
            "HTTP EGF resolution lands with the transport project (#2)"
        )
