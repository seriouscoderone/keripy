"""SAD integrity verification. A source (local dir, HTTP registry) is trusted for
availability, never integrity — every fetched artifact is re-derived and compared."""
import json
from keri.core import coring
from keri_serviceaid.egf.errors import EgfDocumentError, EgfIntegrityError


def verify_sad(raw: bytes, said: str, label: str = "d") -> dict:
    try:
        sad = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError) as ex:
        raise EgfDocumentError(f"unparseable SAD for {said}: {ex}") from ex
    if not isinstance(sad, dict) or sad.get(label) != said:
        raise EgfIntegrityError(said, str(sad.get(label, "")) if isinstance(sad, dict) else "")
    probe = dict(sad); probe[label] = ""
    _, derived = coring.Saider.saidify(sad=probe, label=label)
    if derived[label] != said:
        raise EgfIntegrityError(said, derived[label])
    return sad
