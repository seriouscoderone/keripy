"""Reference Service AID: an allowlist-gated "prove-then-retrieve" service.

EXAMPLE / FICTIONAL. The business compute (``handler_module``) for the Gated
Retrieval Service AID. A caller on the service's allowlist POSTs a signed exn to
``/gated/retrieve``; the framework verifies + authorizes it (sender-AID gating
via ``SERVICEAID_ALLOWLIST``) and hands this function a verified ``Request``.
The function returns a made-up ``gated-record`` ACDC ("cool data") which the
framework issues, signs, and IPEX-grants back to the caller.

The "prove" half (a caller-presented ``gated-access`` credential) is illustrative
only in v1: caller-ACDC extraction is DEFERRED (see the framework handler /
Policy.required_schema), so the gate is enforced by the allowlist. The
``gated_access.json`` schema ships alongside to show the intended shape.

This file is the developer's ``handler_module``: ``runtime.init()`` does
``importlib.import_module("gated_handler")``, so for a real deploy it must sit
in the Lambda asset dir next to the serviceaid runtime (see the bundling note in
keri_cdk/service_aid.py — validated in Task 9).
"""
import json
import pathlib

from keri_cdk.handlers.serviceaid.contract import service, Request, Reply

# Compute the real schema SAID from the bundled schema and queue it for the
# runtime to register into the Habery's schema store at init. This is the ACDC
# the service ISSUES on a successful retrieval.
_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema" / "gated_record.json"
GATED_RECORD_SCHEMA_SAID = service.register_schema(json.loads(_SCHEMA_PATH.read_text()))


def _fetch_record(record_id: str) -> dict:
    """Made-up business lookup. A real service would hit a datastore here; this
    returns fictional "cool data" deterministically derived from the request."""
    return {
        "recordId": record_id or "rec-0001",
        "tier": "premium",
        "data": f"cool data for {record_id or 'rec-0001'}",
    }


@service.command(route="/gated/retrieve", issues=GATED_RECORD_SCHEMA_SAID)
def retrieve(req: Request) -> Reply:
    """Allowlist-gated retrieval: by the time this runs the caller's exn is
    verified and the sender is on the allowlist. Return a made-up gated-record
    ACDC addressed to the caller."""
    requested = req.payload.get("recordId", "")
    record = _fetch_record(requested)
    return Reply.acdc(
        recipient=req.sender,
        attributes={**record, "dt": req.now()},
        # Standalone attestation: not chained to a caller-presented credential.
        # To chain to a presented gated-access ACDC, set edges to
        # {"<edge>": {"cred_said": <linked SAID>, "schema_said": <its schema>}}.
        edges=None,
    )
