"""Reference Service AID: a trivial rating engine."""
import json
import pathlib

from serviceaid import service, Request, Reply

# Compute the real schema SAID from the bundled schema and queue it for the
# runtime to register into the Habery's schema store at init.
_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema" / "rating_result.json"
RATING_SCHEMA_SAID = service.register_schema(json.loads(_SCHEMA_PATH.read_text()))


def _score(profile: dict) -> int:
    base = 800
    base -= int(profile.get("age", 0) < 25) * 50
    base -= int(profile.get("claims", 0)) * 40
    return max(300, min(850, base))


@service.command(route="/rate/apply", issues=RATING_SCHEMA_SAID)
def rate(req: Request) -> Reply:
    score = _score(req.payload.get("risk_profile", {}))
    return Reply.acdc(
        recipient=req.sender,
        attributes={"score": score, "dt": req.now()},
        # To chain this result to an input credential the caller presented,
        # set edges to {"<edge-name>": {"cred_said": <linked ACDC SAID, e.g.
        # req.credentials[0]["said"]>, "schema_said": <that credential's schema
        # SAID>}}. `s` must be the LINKED credential's schema, not this one's.
        # Omitted here: the rating is a standalone attestation.
        edges=None,
    )
