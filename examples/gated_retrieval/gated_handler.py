"""Reference Service-AID: an allowlist-gated "prove-then-retrieve" service.

EXAMPLE / FICTIONAL. The developer's compute_code module. `svc` is the declared
entity; the framework finds it via handler_ref "gated_handler:svc". Two routes:
  - /gated/cmd/request_record → issues a gated-record ACDC (grant on success)
  - /gated/cmd/revoke_record  → acknowledges a revoke request (no reply in v1)

The "prove" half (a caller-presented gated-access credential) is the named
CredentialGate follow-on; v1 enforces the gate with an Allowlist of sender AIDs."""
import json
import pathlib

from keri_serviceaid import ServiceAid, Reply, Request, Allowlist

# Declare the entity. Witnesses/toad come from the deploy (env); the example
# leaves witnesses empty (unwitnessed) for a simple first deploy. allowlist=[]
# means any verified sender (override at deploy via the cdk app context).
svc = ServiceAid(alias="gated", witnesses=[], toad=0, authz=Allowlist([]))

# The ACDC this service ISSUES on a successful retrieval. register_schema
# saidifies it and queues it for the runtime to load into the schema store.
_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema" / "gated_record.json"
GATED_RECORD_SCHEMA_SAID = svc.register_schema(json.loads(_SCHEMA_PATH.read_text()))


def _fetch_record(record_id: str) -> dict:
    """Made-up business lookup ("cool data")."""
    rid = record_id or "rec-0001"
    return {"recordId": rid, "tier": "premium", "data": f"cool data for {rid}"}


@svc.command(route="/gated/cmd/request_record", issues=GATED_RECORD_SCHEMA_SAID)
def request_record(req: Request) -> Reply:
    """Allowlist-gated retrieval: by the time this runs the caller's exn is
    verified and the sender is authorized. Return a gated-record ACDC to the
    caller (the framework issues + grants it to the caller's mailbox)."""
    record = _fetch_record(req.payload.get("recordId", ""))
    return Reply.acdc(recipient=req.sender, attributes={**record, "dt": req.now()})


@svc.command(route="/gated/cmd/revoke_record")
def revoke_record(req: Request) -> Reply:
    """Acknowledge a revoke request. v1 ships grant + silence, so a non-issuing
    command returns Reply.none() (no reply leaves the mailbox). A real service
    would mark the record revoked in its datastore and could (follow-on) emit a
    signed note. Demonstrates a SECOND capability on the same role/AID."""
    # (business effect would go here — e.g. mark req.payload["recordId"] revoked)
    return Reply.none()
