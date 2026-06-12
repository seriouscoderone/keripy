"""End-to-end Lambda handler tests: verify → authorize → dispatch → reply.

A real caller Habery signs a /rate/apply exn, ships its own KEL inline
(self-contained CESR request), and the handler must return a signed IPEX
grant that a FRESH consumer Habery accepts through its own Parser/Exchanger
stack (signature verification included) — the proof of the whole pipeline.
"""
import base64
import json

import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri.app.habbing import Habery
from keri.app.notifying import Notifier
from keri.core.signing import Salter
from keri.core import parsing, scheming, serdering
from keri.kering import Kinds, Vrsn_1_0
from keri.peer import exchanging
from keri.vc import protocoling

from serviceaid import runtime
from serviceaid.config import Config
from serviceaid.contract import service, Reply
from _schema import RATING_SCHEMA_SAD

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def _cfg(**o):
    b = dict(alias="rating", core_table="keri-core",
             keeper_secret="keri/rating/keeper",
             witnesses=[], toad=0, handler_module="",
             region="us-east-1", endpoint_url=None)
    b.update(o)
    return Config(**b)


def _provision_keeper_secret(name="keri/rating/keeper", bran="z" * 21):
    """Create the keeper secret (one secret holding {salt, bran, keeper-blob})
    that the inception CR (Task 6) provisions for a stack."""
    import boto3, json
    from keri.core.signing import Salter
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(
        Name=name,
        SecretString=json.dumps({"v": 1,
                                 "salt": Salter(raw=b'0123456789abcdef').qb64,
                                 "bran": bran, "keeper": None}))


def _caller_request(route: str, attributes: dict) -> tuple[Habery, object, dict]:
    """Build a self-contained CESR request: caller KEL + signed exn.

    Returns (caller_hby, caller_hab, lambda_event). Caller must be closed
    by the test.
    """
    caller_hby = Habery(name="caller", temp=True,
                        salt=Salter(raw=b'caller9876543210').qb64)
    caller = caller_hby.makeHab(name="caller", transferable=True)
    exn, _ = exchanging.exchange(route=route, attributes=attributes,
                                 sender=caller.pre)
    # Caller's KEL first (so the service can verify), then exn + sigs.
    # NOTE: this keripy has no Hab.makeOwnEvent; Hab.replay() (habbing.py)
    # clones the full FEL — here just the inception event.
    ims = bytearray(caller.replay())
    ims.extend(caller.endorse(exn, last=False))  # exn + attached signatures
    event = {"path": route, "httpMethod": "POST",
             "body": base64.b64encode(bytes(ims)).decode(),
             "isBase64Encoded": True}
    return caller_hby, caller, event


def _init_rating_service(score=None):
    """Moto-side service setup: keeper secret + /rate/apply command + schema.

    Must run inside an active mock_aws() context. Returns the RuntimeState.
    """
    _provision_keeper_secret()

    runtime.reset()
    service._commands.clear()
    service.schemas.clear()
    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)

    @service.command(route="/rate/apply", issues=schemer.said)
    def rate(req):
        val = score if score is not None else req.payload.get("risk", 0) * 10
        return Reply.acdc(recipient=req.sender, attributes={"score": val})

    state = runtime.init(_cfg())
    state.hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    return state


@needs_moto
def test_full_request_returns_verifiable_grant(monkeypatch):
    with mock_aws():
        _provision_keeper_secret()

        # Register a command + the schema BEFORE init (handler_module="" => inline).
        runtime.reset()
        service._commands.clear()
        service.schemas.clear()
        schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)
        said = schemer.said

        @service.command(route="/rate/apply", issues=said)
        def rate(req):
            return Reply.acdc(recipient=req.sender,
                              attributes={"score": req.payload["risk"] * 10})

        state = runtime.init(_cfg())
        state.hby.db.schema.pin(keys=(said,), val=schemer)

        from serviceaid import handler as H

        caller_hby, caller, event = _caller_request("/rate/apply", {"risk": 72})
        resp = H.handler(event, None)

        assert resp["statusCode"] == 200
        assert resp["headers"]["Content-Type"] == "application/cesr"
        grant = resp["body"].encode("utf-8")
        assert b"/ipex/grant" in grant

        # Consumer verifies the issued ACDC grant end-to-end: a FRESH Habery
        # learns the issuer's KEL, then must ACCEPT the grant exn through its
        # own Parser/Exchanger (signature verification included) — acceptance
        # is proven by the exn landing in the consumer's exns database
        # (mirrors test_issuing.test_grant_round_trips_through_recipient_parser).
        consumer = Habery(name="consumer", temp=True,
                          salt=Salter(raw=b'consumer87654321').qb64)
        try:
            parsing.Parser(kvy=consumer.kvy, version=Vrsn_1_0).parse(
                ims=state.hab.replay())
            consumer.kvy.processEscrows()
            assert state.hab.pre in consumer.kevers

            gexn = serdering.SerderKERI(raw=bytes(grant))
            assert gexn.ked["r"] == "/ipex/grant"
            assert {"acdc", "iss", "anc"} <= set(gexn.ked["e"])
            assert gexn.ked["e"]["acdc"]["a"]["score"] == 720

            notifier = Notifier(consumer)
            exc = exchanging.Exchanger(hby=consumer, handlers=[])
            protocoling.loadHandlers(consumer, exc=exc, notifier=notifier)
            parsing.Parser(exc=exc, version=Vrsn_1_0).parseOne(ims=bytearray(grant))
            exc.processEscrow()

            stored = consumer.db.exns.get(keys=(gexn.said,))
            assert stored is not None, "grant exn was not accepted by consumer Exchanger"
            assert stored.ked["a"]["i"] == caller.pre   # granted to the caller
        finally:
            caller_hby.close()
            consumer.close()


@needs_moto
def test_duplicate_message_is_idempotent(monkeypatch):
    with mock_aws():
        _provision_keeper_secret()
        runtime.reset()
        service._commands.clear()
        service.schemas.clear()
        schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)

        @service.command(route="/rate/apply", issues=schemer.said)
        def rate(req):
            return Reply.acdc(recipient=req.sender, attributes={"score": 1})

        state = runtime.init(_cfg())
        state.hby.db.schema.pin(keys=(schemer.said,), val=schemer)
        from serviceaid import handler as H

        caller_hby, _, event = _caller_request("/rate/apply", {"risk": 5})
        try:
            r1 = H.handler(event, None)
            n_creds_after_first = len(list(state.rgy.reger.creds.getTopItemIter()))
            r2 = H.handler(event, None)              # duplicate exn SAID
            n_creds_after_second = len(list(state.rgy.reger.creds.getTopItemIter()))
            assert r1["statusCode"] == 200 and r2["statusCode"] == 200
            assert n_creds_after_first == n_creds_after_second   # no re-issue
            # The replay is a JSON duplicate ack, NOT a re-sent grant, and the
            # duplicate marker must survive the merge with the cached summary.
            assert r2["headers"]["Content-Type"] == "application/json"
            assert json.loads(r2["body"])["status"] == "duplicate"
        finally:
            caller_hby.close()


@needs_moto
def test_malformed_base64_body_returns_400():
    with mock_aws():
        _init_rating_service()
        from serviceaid import handler as H

        resp = H.handler({"path": "/rate/apply", "httpMethod": "POST",
                          "body": "a", "isBase64Encoded": True}, None)

        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["error"] == "invalid base64 body"


@needs_moto
def test_unanchored_signature_returns_400():
    """An exn WITHOUT the caller's KEL cannot verify (no key state to anchor
    the signatures) — keripy's Parser swallows the MissingSignatureError, so
    the failure must surface as an empty capture drain => 400."""
    with mock_aws():
        _init_rating_service()
        from serviceaid import handler as H

        caller_hby = Habery(name="caller", temp=True,
                            salt=Salter(raw=b'caller9876543210').qb64)
        try:
            caller = caller_hby.makeHab(name="caller", transferable=True)
            exn, _ = exchanging.exchange(route="/rate/apply",
                                         attributes={"risk": 5},
                                         sender=caller.pre)
            ims = bytearray(caller.endorse(exn, last=False))  # exn only, NO KEL
            event = {"path": "/rate/apply", "httpMethod": "POST",
                     "body": base64.b64encode(bytes(ims)).decode(),
                     "isBase64Encoded": True}

            resp = H.handler(event, None)

            assert resp["statusCode"] == 400
            assert json.loads(resp["body"])["error"] == "no verified exn for route"
        finally:
            caller_hby.close()


@needs_moto
def test_route_mismatch_returns_400():
    """An exn whose embedded route differs from the POSTed path must never be
    processed as the POSTed route (only /rate/apply is registered, so the
    foreign exn finds no behavior and the /rate/apply drain stays empty)."""
    with mock_aws():
        _init_rating_service()
        from serviceaid import handler as H

        caller_hby, _, event = _caller_request("/other/route", {"risk": 5})
        event["path"] = "/rate/apply"   # POSTed path != embedded exn route
        try:
            resp = H.handler(event, None)

            assert resp["statusCode"] == 400
            assert json.loads(resp["body"])["error"] == "no verified exn for route"
        finally:
            caller_hby.close()
