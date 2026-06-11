"""DynamoDB-Local integration smoke test.

Skipped unless SERVICEAID_ENDPOINT_URL is set to a live DynamoDB Local URL.
This exercises the full pipeline (init → request → 200 grant) against a REAL
DynamoDB table rather than moto, with no Secrets Manager (bran_secret="").

Run:
    docker run -p 8000:8000 amazon/dynamodb-local
    SERVICEAID_ENDPOINT_URL=http://localhost:8000 \\
      .venv/bin/python -m pytest service-aid/tests/test_integration_local.py -v
"""
import base64
import os

import pytest

ENDPOINT = os.environ.get("SERVICEAID_ENDPOINT_URL")
needs_local = pytest.mark.skipif(
    not ENDPOINT,
    reason="set SERVICEAID_ENDPOINT_URL to a DynamoDB Local URL",
)


@needs_local
def test_request_against_dynamodb_local(monkeypatch):
    """Full pipeline against a real DynamoDB (Local), not moto.

    Run DynamoDB Local first:
        docker run -p 8000:8000 amazon/dynamodb-local
        SERVICEAID_ENDPOINT_URL=http://localhost:8000 \\
          .venv/bin/python -m pytest service-aid/tests/test_integration_local.py -v
    """
    from keri.app.habbing import Habery
    from keri.core.signing import Salter
    from keri.core import scheming
    from keri.kering import Kinds
    from keri.peer import exchanging
    from serviceaid import runtime, handler as H
    from serviceaid.config import Config
    from serviceaid.contract import service, Reply
    from _schema import RATING_SCHEMA_SAD

    # No Secrets Manager here: run without a bran (plaintext keeper) for the
    # local smoke test; encryption is exercised in the moto suite.
    cfg = Config(
        alias="rating",
        core_table="keri-core-local",
        keeper_table="rating-ks-local",
        witnesses=[],
        toad=0,
        handler_module="",
        bran_secret="",
        region="us-east-1",
        endpoint_url=ENDPOINT,
    )
    runtime.reset()
    service._commands.clear()
    service.schemas.clear()
    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)

    @service.command(route="/rate/apply", issues=schemer.said)
    def rate(req):
        return Reply.acdc(recipient=req.sender, attributes={"score": 700})

    state = runtime.init(cfg)
    state.hby.db.schema.pin(keys=(schemer.said,), val=schemer)

    caller_hby = Habery(
        name="caller", temp=True, salt=Salter(raw=b"caller9876543210").qb64
    )
    caller = caller_hby.makeHab(name="caller", transferable=True)
    exn, _ = exchanging.exchange(
        route="/rate/apply", attributes={"risk": 7}, sender=caller.pre
    )
    # Caller's full KEL first (so the service can verify), then exn + sigs.
    # Matches _caller_request() in test_handler_e2e.py exactly.
    ims = bytearray(caller.replay())
    ims.extend(caller.endorse(exn, last=False))
    event = {
        "path": "/rate/apply",
        "httpMethod": "POST",
        "body": base64.b64encode(bytes(ims)).decode(),
        "isBase64Encoded": True,
    }
    resp = H.handler(event, None)
    assert resp["statusCode"] == 200
    assert b"/ipex/grant" in resp["body"].encode("utf-8")
    caller_hby.close()
