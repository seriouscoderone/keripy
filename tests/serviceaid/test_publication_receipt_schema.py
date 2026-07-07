import json
import pathlib

from keri.core import scheming
from keri.kering import Kinds

SCHEMA = pathlib.Path(__file__).parents[2] / "examples/schema_host/schema/publication_receipt.json"


def test_publication_receipt_schema_saidifies_and_has_attributes():
    sad = json.loads(SCHEMA.read_text())
    schemer = scheming.Schemer(sed=sad, kind=Kinds.json)   # saidifies $id
    assert schemer.said                                     # non-empty SAID
    a = sad["properties"]["a"]
    # attribute object (second oneOf branch) declares the receipt fields
    props = a["oneOf"][1]["properties"]
    for field in ("schemaSaid", "schemaKind", "publisher", "firstSeen",
                  "priorContributor", "origin", "dt"):
        assert field in props
