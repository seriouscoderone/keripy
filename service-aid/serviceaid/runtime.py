"""Cold-start initialization and warm singletons for a Service AID Lambda.

Builds the full keripy stack on DynamoDB (via `keri.app.lambding` setup
helpers), incepts or loads the service AID, ensures its credential registry,
and imports the developer handler module so `@service.command` decorators
populate the `service` singleton.  The resulting `RuntimeState` is cached
module-level so warm Lambda invocations skip all of this.

`init()` must be called from the Lambda handler, never at module import time
(SnapStart safety: the bran is fetched from Secrets Manager inside `init`).

Witnessed-deployment note: the AID is incepted with `wits`/`toad` from config
(production AIDs are witnessed at the KEL layer), but TEL issuance via
`issuing.issue_grant` currently completes only on the unwitnessed path -- see
the WARNING in `serviceaid/issuing.py`'s module docstring.  Tests and the v1
deployment use `wits=[]`; completing TEL events for a witnessed service AID
is deferred work documented there.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from keri.db.dynamodbing import DynamoDBer
from keri.app.lambding import (BASER_STORES, KEEPER_STORES, REGER_STORES,
                               setup_baser, setup_keeper, setup_reger)
from keri.app.habbing import Habery
from keri.app.configing import Configer
from keri.vdr import credentialing

from .config import Config, load_bran
from .contract import service, Service
from .authorize import Policy
from .issuing import ensure_registry
from .idempotency import Ledger, PROC_STORE

logger = logging.getLogger(__name__)

_state = None  # warm singleton across invocations


@dataclass
class RuntimeState:
    cfg: Config
    hby: Habery
    hab: object
    rgy: object
    ledger: Ledger
    svc: Service
    policy: Policy


def reset():
    """Drop the warm singleton (test/maintenance hook)."""
    global _state
    if _state is not None:
        try:
            # Habery.close closes the injected ks and db DynamoDBers + cf.
            _state.hby.close()
        except Exception:
            logger.exception("error closing Habery during reset")
        try:
            # The reger DynamoDBer is not owned by the Habery; close it too.
            _state.rgy.reger.close()
        except Exception:
            logger.exception("error closing reger during reset")
    _state = None


class _CaptureHandler:
    """Exchanger behavior that stashes verified exns for synchronous dispatch."""

    def __init__(self, resource):
        self.resource = resource
        self.captured = []  # list of (serder, attachments)

    def verify(self, serder, attachments=None, **kw):
        return True

    def handle(self, serder, attachments=None, **kw):
        self.captured.append((serder, attachments or []))


def _dynamo_kwa(cfg: Config) -> dict:
    kwa = dict(region=cfg.region)
    if cfg.endpoint_url:
        kwa["endpoint_url"] = cfg.endpoint_url
        # DynamoDB Local / SAM: pin dummy credentials so injected STS session
        # tokens cannot cause UnrecognizedClientException (see sam-witness).
        import boto3
        kwa["session"] = boto3.Session(aws_access_key_id="fake",
                                       aws_secret_access_key="fake",
                                       region_name=cfg.region)
    return kwa


def init(cfg: Config | None = None) -> RuntimeState:
    """Cold start: build Habery on DynamoDB, incept/load the AID + registry,
    import the developer handler. Warm invocations reuse the singleton."""
    global _state
    if _state is not None:
        return _state

    cfg = cfg or Config.from_env()
    kwa = _dynamo_kwa(cfg)

    # Baser (:kel) and Reger (:tel) share the pooled core table under distinct
    # namespaces -- both define a `stts.` store, so they MUST be namespaced
    # apart or key-state and registry-state records would collide.
    db = DynamoDBer.open(name=cfg.alias, stores=BASER_STORES + [PROC_STORE],
                         table_name=cfg.core_table, namespace=cfg.kel_namespace,
                         **kwa)
    setup_baser(db)
    reger = DynamoDBer.open(name=cfg.alias, stores=REGER_STORES,
                            table_name=cfg.core_table,
                            namespace=cfg.tel_namespace, **kwa)
    setup_reger(reger)

    # Keeper: isolated table, encrypted via bran (aeid). Never pooled.
    ks = DynamoDBer.open(name=f"{cfg.alias}-ks", stores=KEEPER_STORES,
                         table_name=cfg.keeper_table, **kwa)
    setup_keeper(ks)

    bran = load_bran(cfg.bran_secret, region=cfg.region) if cfg.bran_secret else None

    cf = Configer(name=cfg.alias, temp=True)  # Lambda: filesystem only in /tmp
    hby = Habery(name=cfg.alias, temp=False, free=True, db=db, ks=ks, cf=cf,
                 bran=bran)

    hab = hby.habByName(cfg.alias)
    if hab is None:
        # Incepted with wits/toad from config (witnessed at the KEL layer in
        # production).  NOTE: issuance via `issuing.issue_grant` currently
        # requires the unwitnessed path (tests use wits=[]); for a WITNESSED
        # service AID the TEL witness-receipt escrow cannot complete on the
        # synchronous virtual-time Doist -- see the WARNING in
        # serviceaid/issuing.py.  Witnessed-deployment completion is deferred.
        hab = hby.makeHab(name=cfg.alias, transferable=True,
                          wits=cfg.witnesses, toad=cfg.toad,
                          isith="1", icount=1, nsith="1", ncount=1)
    # Hab.make adds the prefix on first creation, but the lambding setup_baser
    # does not repopulate db.prefixes from stored state on later cold starts.
    hby.prefixes.add(hab.pre)

    rgy = credentialing.Regery(hby=hby, name=cfg.alias, reger=reger)
    ensure_registry(hby, hab, rgy, name=cfg.alias)

    svc = service
    if cfg.handler_module:
        importlib.import_module(cfg.handler_module)  # decorators populate `service`
    # Register the developer's ACDC schemas so Credentialer.create can validate.
    from keri.core import scheming
    from keri.kering import Kinds
    for sad in svc.schemas:
        schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
        if hby.db.schema.get(keys=(schemer.said,)) is None:
            hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    for route in svc.routes:
        hby.exc.addHandler(_CaptureHandler(resource=route))

    policy = Policy(allowlist=cfg.allowlist, required_schema=cfg.required_schema)
    _state = RuntimeState(cfg=cfg, hby=hby, hab=hab, rgy=rgy,
                          ledger=Ledger(db), svc=svc, policy=policy)
    return _state
