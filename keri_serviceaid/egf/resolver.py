"""Verifying resolver over an `EgfSource`.

Every `resolve_*` call fetches raw bytes from the source, re-derives the SAID via
`verify_sad` (fail-closed: `EgfIntegrityError` on any mismatch, `EgfDocumentError`
on unparseable/malformed content), and only THEN is the result eligible for caching.
A SAID is content-addressed and immutable, so once verified, a result never needs
to be re-fetched or re-verified for the lifetime of the resolver.
"""
from keri_serviceaid.egf.documents import EgfDocument
from keri_serviceaid.egf.errors import EgfDocumentError
from keri_serviceaid.egf.source import EgfSource
from keri_serviceaid.egf.verify import verify_sad


class EgfResolver:
    def __init__(self, source: EgfSource):
        self._source = source
        self._cache: dict[str, object] = {}

    def resolve_egf(self, said: str) -> EgfDocument:
        """Resolve and verify an egf-doc/0.1 EGF document, typed via `EgfDocument`."""
        cached = self._cache.get(said)
        if cached is not None:
            return cached
        raw = self._source.fetch(said)
        sad = verify_sad(raw, said, label="d")
        doc = EgfDocument.from_sad(sad)
        self._cache[said] = doc
        return doc

    def resolve_schema(self, said: str) -> dict:
        """Resolve and verify a JSON-Schema artifact (labeled `$id`).

        ACDC schema-versioning requires every schema to carry a `version` string;
        absence is a document-shape defect, not an integrity failure, so it raises
        `EgfDocumentError` rather than `EgfIntegrityError`.
        """
        cached = self._cache.get(said)
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
        self._cache[said] = sad
        return sad

    def resolve_micro_app(self, said: str) -> dict:
        """Resolve and verify a micro-app artifact (labeled `d`), as a plain dict."""
        cached = self._cache.get(said)
        if cached is not None:
            return cached
        raw = self._source.fetch(said)
        sad = verify_sad(raw, said, label="d")
        self._cache[said] = sad
        return sad
