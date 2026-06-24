"""Cold-start initialization + warm singleton for a Service-AID Lambda.

Opens the Baser `db` on the shared oracle namespace + own private ns; opens the
Reger PRIVATE (credential bodies never pool); keeper from Secrets Manager; builds
Habery; incepts-or-loads the witnessed AID collecting receipts via Receiptor
(NEVER WitnessReceiptor); ensures the registry; publishes own end-role; imports
the dev's compute_code module via handler_ref (module:attr) → svc; wires default
providers for any the dev left None; registers schemas; adds a capture behavior
per route to hby.exc; returns RuntimeState.

init() must be called from the handler, never at module import (SnapStart safety:
the keeper secret is fetched inside init)."""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from hio.base import doing

from keri.db.dynamodbing import DynamoDBer
from keri.db.secretkeeper import SecretStore, SecretKeeper
from keri.app.lambding import (BASER_STORES, REGER_STORES, SHARED_KEL_STORES,
                               setup_baser, setup_keeper, setup_reger)
from keri.app.habbing import Habery
from keri.app.configing import Configer
from keri.app import agenting
from keri.kering import Roles
from keri.vdr import credentialing

from .config import Config
from .contract import ServiceAid
from ._capture import _CaptureHandler
from .providers import (Allowlist, OracleVerifier, OracleResolver,
                       IpexGrantIssuer, PostmanDeliverer, DynamoLedger)
from .providers.idempotency import PROC_STORE
from .providers.issue import ensure_registry

logger = logging.getLogger(__name__)

_state = None  # warm singleton across invocations


@dataclass
class RuntimeState:
    cfg: Config
    hby: Habery
    hab: object          # the service Hab (Habery.makeHab return type)
    rgy: object          # credentialing.Regery
    svc: ServiceAid


def reset():
    """Drop the warm singleton (test/maintenance + inception CR hook)."""
    global _state
    if _state is not None:
        try:
            _state.hby.close()
        except Exception:
            logger.exception("error closing Habery during reset")
        try:
            _state.rgy.reger.close()
        except Exception:
            logger.exception("error closing reger during reset")
    _state = None


def _dynamo_kwa(cfg: Config) -> dict:
    kwa = dict(region=cfg.region)
    if cfg.endpoint_url:
        kwa["endpoint_url"] = cfg.endpoint_url
        import boto3
        kwa["session"] = boto3.Session(aws_access_key_id="fake",
                                       aws_secret_access_key="fake",
                                       region_name=cfg.region)
    return kwa


def _wire_default_providers(svc: ServiceAid, *, db) -> None:
    """Instantiate the default impl for any provider the dev left None."""
    if svc.authz is None:
        svc.authz = Allowlist([])
    if svc.verifier is None:
        svc.verifier = OracleVerifier(tier="receipts")
    if svc.resolver is None:
        svc.resolver = OracleResolver()
    if svc.issuer is None:
        svc.issuer = IpexGrantIssuer()
    if svc.deliverer is None:
        svc.deliverer = PostmanDeliverer()
    if svc.idempotency is None:
        svc.idempotency = DynamoLedger(db)


def incept_or_load(hby, cfg: Config):
    """Load the service hab by alias, or incept it WITNESSED, collecting its own
    receipts via agenting.Receiptor (POST /receipts). The push-mode alternative
    silently hangs over HTTP/Lambda (keripy#1422, locksmith#77)."""
    hab = hby.habByName(cfg.alias)
    if hab is not None:
        hby.prefixes.add(hab.pre)
        return hab

    with hby.ks.deferflush():     # single atomic keeper write on incept
        hab = hby.makeHab(name=cfg.alias, transferable=True,
                          wits=cfg.witnesses, toad=cfg.toad,
                          isith="1", icount=1, nsith="1", ncount=1)
    hby.prefixes.add(hab.pre)

    if hab.kever.wits:
        # Synchronous /receipts collection. Drive Receiptor the keripy-native
        # way: yield-from receiptor.receipt(...) inside a Doer that the Doist
        # schedules, then run the Doist via .do(limit=30) so real-time pacing
        # AND the 30s deadline are actually enforced (a hand-rolled next()/recur
        # loop bypasses both — real sleeping and the limit check live only in
        # Doist.do). If witnesses are unreachable, .do returns at the deadline
        # rather than burning the whole Lambda timeout.
        receiptor = agenting.Receiptor(hby=hby)

        def _collect(tymth=None, tock=0.0, **kwa):
            yield from receiptor.receipt(hab.pre, sn=0)

        collector = doing.doify(_collect)
        doist = doing.Doist(real=True, tock=0.03125, limit=30.0)
        doist.do(doers=[receiptor, collector], limit=30.0)
    return hab


def _publish_end_role_and_oobi(hby, hab):
    """Publish the service AID's own controller end-role so requesters can reach
    it (and so endsFor on peers resolves). Best-effort; logged on failure."""
    try:
        rpy = hab.makeEndRole(eid=hab.pre, role=Roles.controller)
        hby.psr.parse(ims=bytearray(rpy))
    except Exception:
        logger.exception("failed to publish own end-role (non-fatal)")


def init(cfg: Config | None = None) -> RuntimeState:
    """Cold start: open the keripy stack on DynamoDB (shared KEL oracle + private
    namespaces), open the keeper from Secrets Manager, build the Habery,
    incept-or-load the witnessed AID + registry, import the developer's
    compute_code module (handler_ref module:attr), wire default providers, and
    register a capture behavior per route. Warm invocations reuse the singleton.
    Never call at module import time (the keeper secret is fetched here)."""
    global _state
    if _state is not None:
        return _state

    cfg = cfg or Config.from_env()
    kwa = _dynamo_kwa(cfg)

    # Baser db: own private ns + the shared oracle ns for public KEL stores.
    db = DynamoDBer.open(name=cfg.alias, stores=BASER_STORES + [PROC_STORE],
                         table_name=cfg.core_table, namespace=cfg.kel_namespace,
                         shared_namespace="shared", shared_stores=SHARED_KEL_STORES,
                         **kwa)
    setup_baser(db)
    # Reger: PRIVATE — credential bodies/TEL never pool (no shared args).
    reger = DynamoDBer.open(name=cfg.alias, stores=REGER_STORES,
                            table_name=cfg.core_table,
                            namespace=cfg.tel_namespace, **kwa)
    setup_reger(reger)

    store = SecretStore(region=cfg.region, endpoint_url=cfg.secret_endpoint_url)
    ks = SecretKeeper.open(store=store, secret_name=cfg.keeper_secret)
    setup_keeper(ks)
    if not ks.bran:
        logger.warning("keeper secret %s has no bran — keeper UNENCRYPTED",
                       cfg.keeper_secret)

    cf = Configer(name=cfg.alias, temp=True)  # Lambda: filesystem only in /tmp
    hby = Habery(name=cfg.alias, temp=False, free=True, db=db, ks=ks, cf=cf,
                 salt=ks.salt, bran=ks.bran)

    hab = incept_or_load(hby, cfg)
    rgy = credentialing.Regery(hby=hby, name=cfg.alias, reger=reger)
    ensure_registry(hby, hab, rgy, name=cfg.alias)
    _publish_end_role_and_oobi(hby, hab)

    # Import the dev compute_code module and grab the ServiceAid via module:attr.
    if not cfg.handler_ref or ":" not in cfg.handler_ref:
        raise ValueError(f"SERVICEAID_HANDLER must be 'module:attr' (got "
                         f"{cfg.handler_ref!r})")
    module_name, attr = cfg.handler_ref.split(":", 1)
    module = importlib.import_module(module_name)
    svc = getattr(module, attr)
    if not isinstance(svc, ServiceAid):
        raise TypeError(f"{cfg.handler_ref} did not resolve to a ServiceAid")

    _wire_default_providers(svc, db=db)

    # Register the dev's ACDC schemas so Credentialer.create can validate.
    from keri.core import scheming
    from keri.kering import Kinds
    for sad in svc.schemas:
        schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
        if hby.db.schema.get(keys=(schemer.said,)) is None:
            hby.db.schema.pin(keys=(schemer.said,), val=schemer)

    # One capture behavior per route (dispatch reads the captured verified exn).
    for route in svc.routes:
        hby.exc.addHandler(_CaptureHandler(resource=route))

    _state = RuntimeState(cfg=cfg, hby=hby, hab=hab, rgy=rgy, svc=svc)
    return _state
