"""The Service-AID db (Baser) shares its KEL stores; the reger (Reger) stays private."""
import keri_cdk.handlers.serviceaid.runtime as rt
from keri.app.lambding import SHARED_KEL_STORES


def test_serviceaid_db_open_shares_kel_reger_private(monkeypatch):
    calls = []

    def fake_open(*a, **kw):
        calls.append(kw)
        raise SystemExit

    monkeypatch.setattr("keri.db.dynamodbing.DynamoDBer.open", fake_open)
    # minimal cfg stub (init() reaches the `db` open after _dynamo_kwa(cfg),
    # which uses only region/endpoint_url); init signature is init(cfg=None).
    class Cfg:
        alias = "gated"; core_table = "keri-core"; kel_namespace = "gated:kel"
        tel_namespace = "gated:tel"; region = "us-east-1"; endpoint_url = None
    monkeypatch.setattr(rt, "_state", None, raising=False)
    try:
        rt.init(Cfg())
    except SystemExit:
        pass
    assert calls, "init() did not reach the db open"
    db_kw = calls[0]   # the FIRST open is the Baser `db` (shared); reger is second (private)
    assert db_kw.get("shared_namespace") == "shared"
    assert db_kw.get("shared_stores") == SHARED_KEL_STORES
