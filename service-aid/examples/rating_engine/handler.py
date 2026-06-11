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
        edges={"profile": {"cred_said": req.payload_said,
                           "schema_said": RATING_SCHEMA_SAID}} if req.payload_said else None,
    )
