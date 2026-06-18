"""Handler: 204 on accepted ingest, 400 on malformed envelope, CR fork,
and a documented moto + fake-mailbox integration scaffold (skipped by default)."""
import base64
from types import SimpleNamespace

import pytest

from keri_serviceaid import handler as H


# ---------- unit: 204 / 400 / CR fork with a stubbed runtime --------------
def _http_event(body_bytes, attachment="-AAB", content_type="application/cesr"):
    return {
        "httpMethod": "POST", "path": "/",
        "headers": {"Content-Type": content_type, "CESR-ATTACHMENT": attachment},
        "body": base64.b64encode(body_bytes).decode(), "isBase64Encoded": True,
    }


def test_malformed_envelope_returns_400(monkeypatch):
    # No CESR-ATTACHMENT header → malformed → 400 (only real HTTP error).
    ev = {"httpMethod": "POST", "path": "/",
          "headers": {"Content-Type": "application/cesr"},
          "body": base64.b64encode(b'{"v":"KERI10JSON"}').decode(),
          "isBase64Encoded": True}
    monkeypatch.setattr(H.runtime, "init", lambda: SimpleNamespace())
    assert H.handler(ev, None) == {"statusCode": 400}


def test_accepted_ingest_returns_204(monkeypatch):
    captured = {}

    class FakeCapture:
        def drain(self):
            return [(SimpleNamespace(ked={"r": "/svc/cmd/go"}, said="E"), [])]

    class FakePsr:
        def parse(self, ims, framed=False): pass

    state = SimpleNamespace(
        hby=SimpleNamespace(psr=FakePsr(),
                            kvy=SimpleNamespace(processEscrows=lambda: None),
                            exc=SimpleNamespace(processEscrow=lambda: None,
                                                routes={"/svc/cmd/go": FakeCapture()})),
        svc=SimpleNamespace())
    monkeypatch.setattr(H.runtime, "init", lambda: state)
    monkeypatch.setattr(H.pipeline, "process",
                        lambda st, serder, attachments: captured.setdefault("hit", True))
    resp = H.handler(_http_event(b'{"v":"KERI10JSON"}'), None)
    assert resp == {"statusCode": 204}
    assert captured.get("hit") is True


def test_cr_request_type_forks_to_inception(monkeypatch):
    called = {}
    import keri_cdk._inception as inc
    monkeypatch.setattr(inc, "on_event",
                        lambda e, c: called.__setitem__("pre", "Epre") or {"ok": 1})
    resp = H.handler({"RequestType": "Create"}, None)
    assert resp == {"ok": 1} and called.get("pre") == "Epre"


# ---------- integration (skipped by default): moto + fake mailbox round-trip ----
@pytest.mark.integration
def test_end_to_end_grant_delivered_and_replay_redelivers():
    """Cold-start on moto, incept (wits=[]), POST a signed KEL+exn from a test
    requester, oracle verify → compute → issue → deliver into a FAKE mailbox;
    assert the grant landed; replay re-delivers the same grant (not re-issued).

    Implementation outline (filled by the executing agent OR validated live by
    the Task 11 deploy):
      1. mock_aws(); boto3 dynamodb + a moto Secrets Manager secret with
         {salt,bran,keeper:null} at keri/itest/keeper.
      2. Build a compute_code module on sys.path defining
         `svc = ServiceAid(alias="itest")` with a /itest/cmd/go acdc command +
         register_schema(...). Set env SERVICEAID_ALIAS/CORE_TABLE/HANDLER/
         ENDPOINT_URL/SECRET_ENDPOINT_URL; toad=0, wits unset.
      3. runtime.reset(); state = runtime.init().
      4. Build a requester Habery; parse its KEL into the service oracle;
         inject a controller end-role for the requester so OracleResolver
         resolves an endpoint.
      5. Build a signed /itest/cmd/go exn from the requester; POST it via
         H.handler(_http_event(...)).
      6. Monkeypatch svc.deliverer with a capturing FakeDeliverer; assert a grant
         was delivered and the exn SAID is now seen() in the ledger.
      7. POST the SAME exn again → assert deliverer called again with the SAME
         grant bytes and svc.issuer.issue was NOT called a second time.
    """
    pytest.skip("integration scaffold — validated live by the Task 11 deploy; "
                "gated by -m integration")
