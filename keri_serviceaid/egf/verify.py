"""SAD integrity verification. A source (local dir, HTTP registry) is trusted for
availability, never integrity — every fetched artifact is re-derived and compared."""
import json
from keri.core import coring
from keri_serviceaid.egf.errors import EgfDocumentError, EgfIntegrityError


def verify_sad(raw: bytes, said: str, label: str = "d") -> dict:
    """Re-derive the SAID from `raw` and compare against `said`, failing closed on any mismatch.

    Verification re-derives over the document's as-parsed (insertion) order, per KERI SAD
    semantics — a SAD commits to its serialized field order, not to some canonical sort. The
    authoring toolchain (saidify.py) sorts keys before WRITING, so its artifacts verify as-parsed
    as well. Do not re-sort at verify time: that would break the ecosystem's pinned schema SAIDs.
    """
    try:
        sad = json.loads(raw.decode())
    except ValueError as ex:
        raise EgfDocumentError(f"unparseable SAD for {said}: {ex}") from ex
    if not isinstance(sad, dict):
        raise EgfIntegrityError(said, "")
    probe = dict(sad); probe[label] = ""
    _, derived = coring.Saider.saidify(sad=probe, label=label)
    if derived[label] != said:
        raise EgfIntegrityError(said, derived[label])
    if sad.get(label) != said:
        # Content re-derives correctly, but the embedded label field itself was altered.
        # derived[label] is the honest computed SAID for this content.
        raise EgfIntegrityError(said, derived[label])
    return sad
