"""The Service-AID db (Baser) shares its KEL stores; the reger (Reger) stays private."""
import keri_cdk.handlers.serviceaid.runtime as rt
from keri.app.lambding import SHARED_KEL_STORES


def test_serviceaid_db_open_shares_kel_reger_private(monkeypatch):
    """init() opens TWO DynamoDBers: the Baser `db` (shared KEL) FIRST, then the
    Reger `reger` (TEL + credential bodies) SECOND. The db open must carry the
    shared args; the reger open must carry NONE (credential bodies never pool)."""
    calls = []

    def fake_open(*a, **kw):
        calls.append(kw)
        if len(calls) >= 2:      # let the db open (1st) AND reger open (2nd) be
            raise SystemExit     # captured, then short-circuit before Habery build
        return None              # db sentinel; setup_baser is stubbed below

    monkeypatch.setattr("keri.db.dynamodbing.DynamoDBer.open", fake_open)
    # setup_baser runs between the two opens on the sentinel db — stub it so the
    # None sentinel can't crash before the reger open is captured.
    monkeypatch.setattr(rt, "setup_baser", lambda *a, **k: None)
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
    assert len(calls) >= 2, "init() did not reach both the db and reger opens"
    db_kw, reger_kw = calls[0], calls[1]
    # db (Baser) routes its public KEL stores into the shared oracle namespace
    assert db_kw.get("shared_namespace") == "shared"
    assert db_kw.get("shared_stores") == SHARED_KEL_STORES
    # reger (Reger) is fully private — credential bodies must NEVER be shared
    assert "shared_namespace" not in reger_kw, "reger open must not share its namespace"
    assert "shared_stores" not in reger_kw, "reger open must not share any stores"
    assert reger_kw.get("namespace") == "gated:tel"  # stays in the private TEL namespace
