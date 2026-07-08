"""Cold-start runtime: env config, default-provider wiring, cross-Habery oracle
read, and the Receiptor (never WitnessReceiptor) regression guard."""
import inspect

import boto3
from moto import mock_aws

from keri.db.dynamodbing import DynamoDBer
from keri.app.lambding import BASER_STORES, SHARED_KEL_STORES, setup_baser
from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import parsing
from keri.kering import Vrsn_1_0

from keri_serviceaid import config, runtime
from keri_serviceaid import (Allowlist, OracleVerifier, OracleResolver,
                             IpexGrantIssuer, PostmanDeliverer, DynamoLedger)


def test_config_from_env_parses_handler_ref(monkeypatch):
    monkeypatch.setenv("SERVICEAID_ALIAS", "gated")
    monkeypatch.setenv("SERVICEAID_CORE_TABLE", "keri-core")
    monkeypatch.setenv("SERVICEAID_HANDLER", "gated_handler:svc")
    monkeypatch.setenv("SERVICEAID_WITNESSES", "EWit1,EWit2")
    monkeypatch.setenv("SERVICEAID_TOAD", "2")
    cfg = config.Config.from_env()
    assert cfg.alias == "gated"
    assert cfg.handler_ref == "gated_handler:svc"
    assert cfg.witnesses == ["EWit1", "EWit2"]
    assert cfg.toad == 2
    assert cfg.keeper_secret == "keri/gated/keeper"
    assert cfg.kel_namespace == "gated:kel" and cfg.tel_namespace == "gated:tel"


def test_init_wires_default_providers_for_none(monkeypatch):
    """A ServiceAid with all providers None gets defaults instantiated by init.
    We exercise only the default-wiring helper (the heavy keripy build is covered
    by the handler integration test in Task 6).

    DynamoLedger.__init__ eagerly constructs a Suber, which needs a real db.
    Monkeypatch it to a no-op so the wiring test stays unit-level (the real
    DynamoLedger on a real db is exercised in the integration path)."""
    from keri_serviceaid.contract import ServiceAid
    from keri_serviceaid.providers import idempotency as _idm
    monkeypatch.setattr(_idm.DynamoLedger, "__init__",
                        lambda self, db: setattr(self, "db", db) or None)
    svc = ServiceAid(alias="gated")
    fake_db = object()
    runtime._wire_default_providers(svc, db=fake_db)
    assert isinstance(svc.authz, Allowlist)
    assert isinstance(svc.verifier, OracleVerifier) and svc.verifier.tier == "receipts"
    assert isinstance(svc.resolver, OracleResolver)
    assert isinstance(svc.issuer, IpexGrantIssuer)
    assert isinstance(svc.deliverer, PostmanDeliverer)
    assert isinstance(svc.idempotency, DynamoLedger)


def test_incept_or_load_uses_receiptor_not_witnessreceiptor():
    """Regression guard for keripy#1422 / locksmith#77: the inception code path
    must reference Receiptor (sync /receipts) and never WitnessReceiptor."""
    src = inspect.getsource(runtime.incept_or_load)
    assert "Receiptor" in src
    assert "WitnessReceiptor" not in src, (
        "inception must use Receiptor (/receipts), not WitnessReceiptor — the "
        "direct-mode push assumption silently hangs over HTTP/Lambda")


def test_cross_habery_oracle_read_kever_visible():
    """Two DynamoDBers sharing the `shared` namespace on one moto table: AID-A's
    key-state (parsed through service-A's db) is visible to service-B from the
    pooled shared namespace.

    The oracle pools the KEL digest index + key-state (kels./stts./ksns.), NOT
    the per-witness event/receipt write-logs (evts./sigs./wigs./... stay node-
    private so keripy's Receiptor converges receipts across the witness pool).
    So cross-service visibility is asserted via the shared KEL index (kels.),
    which a consumer reads to learn a peer's current key state."""
    with mock_aws():
        # Ensure the table is created via the first DynamoDBer open.
        def _open(ns):
            d = DynamoDBer.open(name=ns, stores=BASER_STORES, table_name="keri-core",
                                namespace=ns, shared_namespace="shared",
                                shared_stores=SHARED_KEL_STORES, region="us-east-1")
            setup_baser(d)
            return d

        # A producer AID whose KEL we publish into the shared oracle.
        prod = Habery(name="prod", temp=True, salt=Salter(raw=b'aaaaaaaaaaaaaaaa').qb64)
        # TRANSITIONAL v1 hold (see conftest.recipient_pre): makeHab ignores hby.version,
        # so pin the KEL to v1 for the v1 Parser below. Lift with the v2 registry+IPEX effort.
        producer = prod.makeHab(name="prod", transferable=True, version=Vrsn_1_0)
        pre = producer.pre
        kel = bytearray(producer.replay())
        prod.close()

        # service-A parses the KEL into ITS db (shared stores route to shared#).
        from keri.core.eventing import Kevery
        dbA = _open("svca:kel")
        kvy = Kevery(db=dbA)
        parsing.Parser(kvy=kvy, version=Vrsn_1_0).parse(ims=bytearray(kel))

        # service-B opens its OWN private ns but the SAME shared oracle table.
        dbB = _open("svcb:kel")
        # The producer's key-STATE (KeyStateRecord) lives in the shared `stts.`
        # store — Kever pins it on each processed event — readable from B's view
        # of the pooled oracle. This is what an oracle consumer reads to learn a
        # peer's current keys (the verifiable key state, not the full event log).
        ksr = dbB.states.get(keys=(pre,))
        assert ksr is not None and ksr.i == pre, (
            f"producer key-state not visible to service-B via the shared oracle "
            f"namespace (prefix={pre!r})")
        # Flip side of the same invariant: the per-witness receipt/event WRITE-logs
        # (including the v2 vrcsNew store) are NOT pooled, so each witness keeps its
        # own receipts and keri.app.agenting.Receiptor can converge toad-of-N. A
        # future edit that pools any of these must fail here loudly.
        assert {"vrcs.", "vrcsnew.", "wigs.", "rcts.", "evts.", "sigs."}.isdisjoint(
            SHARED_KEL_STORES)
        dbA.close(); dbB.close()
