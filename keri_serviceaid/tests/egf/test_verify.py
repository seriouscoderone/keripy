import json
import pytest
from keri.core import coring
from keri_serviceaid.egf.errors import EgfIntegrityError, EgfDocumentError
from keri_serviceaid.egf.verify import verify_sad


def _saidified(doc: dict, label: str = "d") -> tuple[str, bytes]:
    doc = dict(doc); doc[label] = ""
    saider, sad = coring.Saider.saidify(sad=doc, label=label)
    return sad[label], json.dumps(sad).encode()

def test_valid_sad_verifies_and_returns_dict():
    said, raw = _saidified({"d": "", "spec_version": "egf-doc/0.1", "x": 1})
    assert verify_sad(raw, said)["x"] == 1

def test_tampered_content_raises_integrity_error():
    said, raw = _saidified({"d": "", "x": 1})
    bad = raw.replace(b'"x": 1', b'"x": 2')
    with pytest.raises(EgfIntegrityError) as ei:
        verify_sad(bad, said)
    assert ei.value.said == said

def test_wrong_said_raises_integrity_error():
    _, raw = _saidified({"d": "", "x": 1})
    with pytest.raises(EgfIntegrityError):
        verify_sad(raw, "E" + "A" * 43)

def test_schema_label_dollar_id():
    said, raw = _saidified({"$id": "", "title": "T"}, label="$id")
    assert verify_sad(raw, said, label="$id")["title"] == "T"

def test_malformed_json_raises_document_error():
    with pytest.raises(EgfDocumentError):
        verify_sad(b"{not json", "E" + "A" * 43)

def test_verification_is_over_as_parsed_order_not_resorted():
    """SADs commit to their serialized insertion order (KERI convention).
    The ecosystem's pinned schemas verify ONLY over as-parsed order; recursive
    key-sorting at verify time would break them. Do not 'fix' verify_sad to sort."""
    doc = {"d": "", "zeta": 1, "alpha": 2}          # deliberately NOT sorted
    saider, sad = coring.Saider.saidify(sad=doc, label="d")
    raw = json.dumps(sad).encode()                   # preserves insertion order
    assert verify_sad(raw, sad["d"])["zeta"] == 1    # as-parsed verifies

    resorted = {k: sad[k] for k in sorted(sad)}      # same content, sorted order
    raw_sorted = json.dumps(resorted).encode()
    with pytest.raises(EgfIntegrityError):
        verify_sad(raw_sorted, sad["d"])             # different serialization = different SAID
