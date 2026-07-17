"""Verifying resolver over an `EgfSource`.

Every `resolve_*` call fetches raw bytes from the source, re-derives the SAID via
`verify_sad` (fail-closed: `EgfIntegrityError` on any mismatch, `EgfDocumentError`
on unparseable/malformed content), and only THEN is the result eligible for caching.
A SAID is content-addressed and immutable, so once verified, a result never needs
to be re-fetched or re-verified for the lifetime of the resolver. The cache is keyed
by `(kind, said)` — e.g. `("egf", said)`, `("schema", said)`, `("micro_app", said)` —
not by bare SAID, so a SAID resolved via one method never returns a cached value of
a different artifact kind to another method; each `resolve_*` call always goes
through full fetch + verify for its own kind.
"""
from keri_serviceaid.egf.documents import EgfDocument
from keri_serviceaid.egf.errors import EgfDocumentError
from keri_serviceaid.egf.source import EgfSource
from keri_serviceaid.egf.verify import verify_sad


class EgfResolver:
    def __init__(self, source: EgfSource):
        self._source = source
        self._cache: dict[tuple[str, str], object] = {}

    def resolve_egf(self, said: str) -> EgfDocument:
        """Resolve and verify an egf-doc/0.1 EGF document, typed via `EgfDocument`."""
        key = ("egf", said)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        raw = self._source.fetch(said)
        sad = verify_sad(raw, said, label="d")
        doc = EgfDocument.from_sad(sad)
        self._cache[key] = doc
        return doc

    def resolve_schema(self, said: str) -> dict:
        """Resolve and verify a JSON-Schema artifact (labeled `$id`).

        ACDC schema-versioning requires every schema to carry a `version` string;
        absence is a document-shape defect, not an integrity failure, so it raises
        `EgfDocumentError` rather than `EgfIntegrityError`.
        """
        key = ("schema", said)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        raw = self._source.fetch(said)
        sad = verify_sad(raw, said, label="$id")
        version = sad.get("version")
        if not isinstance(version, str) or not version:
            raise EgfDocumentError(
                f"schema {said} is missing a required 'version' string "
                "(ACDC schema-versioning requirement)"
            )
        self._cache[key] = sad
        return sad

    def resolve_micro_app(self, said: str) -> dict:
        """Resolve and verify a micro-app artifact (labeled `d`), as a plain dict."""
        key = ("micro_app", said)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        raw = self._source.fetch(said)
        sad = verify_sad(raw, said, label="d")
        self._cache[key] = sad
        return sad
