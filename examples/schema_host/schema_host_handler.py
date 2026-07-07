"""schema.keri.host — the publish_schema Service-AID (compute_code module).

`svc` is the declared entity; the framework finds it via handler_ref
"schema_host_handler:svc". One command: /schema/cmd/publish stores a public ACDC
schema in the CAS and records the publication (issuing an optional
publication_receipt ACDC). v1 gate = an Allowlist of publisher AIDs (override at
deploy). Accepts ACDC schemas ONLY; never private ACDC instances."""
import json
import pathlib

from keri.core import scheming
from keri.kering import Kinds

from keri_serviceaid import ServiceAid, Reply, Request, Allowlist

# v1 allowlist is injected at deploy (empty here = any verified sender; the cdk
# app sets the real publisher AIDs via a gitignored config / context).
svc = ServiceAid(alias="schema-publisher", witnesses=[], toad=0, authz=Allowlist([]))

_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema" / "publication_receipt.json"
RECEIPT_SCHEMA_SAID = svc.register_schema(json.loads(_SCHEMA_PATH.read_text()))


def validate_public_schema(sad: dict) -> None:
    """Guardrail: accept only a well-formed ACDC/JSON schema whose $id == its SAID.

    Rejects anything lacking the JSON-Schema markers ($id/$schema) — which
    includes ACDC *instances* (they carry `d`/`i`/`a`, not `$id`), keeping
    private subject data out of the public CAS. Raises ValueError on rejection."""
    if not isinstance(sad, dict) or "$id" not in sad or "$schema" not in sad:
        raise ValueError("not a JSON Schema SAD (missing $id/$schema) — "
                         "instances and non-schema SADs are refused")
    # Schemer(verify=True) recomputes the SAID and checks it equals $id.
    scheming.Schemer(sed=dict(sad), kind=Kinds.json)


@svc.command(route="/schema/cmd/publish", issues=RECEIPT_SCHEMA_SAID)
def publish_schema(req: Request) -> Reply:
    """Validate + publish an ACDC schema. The framework stores it in the CAS,
    issues a publication_receipt ACDC (the KEL-anchored ledger entry), and
    delivers the receipt iff the caller asked (`want_receipt`)."""
    sad = req.payload["schema"]
    validate_public_schema(sad)
    schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
    return Reply.publish(
        recipient=req.sender,
        artifact_said=schemer.said,
        artifact_bytes=schemer.raw,
        attributes={"schemaSaid": schemer.said, "schemaKind": "ACDC-schema",
                    "publisher": req.sender, "origin": req.payload.get("origin")},
        want_receipt=bool(req.payload.get("want_receipt", False)),
    )
