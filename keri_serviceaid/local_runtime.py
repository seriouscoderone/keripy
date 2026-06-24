"""LocalRuntime — the in-wallet adapter.

Drives the keri_serviceaid pipeline against a wallet's LMDB Habery and ONE bound
AID (hab) + its registry (rgy). Wires the local-variant providers, registers a
capture handler per command route on hby.exc, and drains them through
pipeline.process. The live mailbox transport is added in a later task; for
tests/headless use, feed exns to the exchanger and call process_captured().

No DynamoDB, no Qt — pure keripy."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from keri.app.indirecting import MailboxDirector
from keri.vdr import verifying

from . import pipeline
from ._capture import _CaptureHandler
from .contract import ServiceAid
from .providers import (Allowlist, OracleVerifier, BoundResolver,
                       IpexGrantIssuer, PostmanDeliverer, LMDBLedger, CredentialGate)

logger = logging.getLogger(__name__)


@dataclass
class LocalCfg:
    alias: str            # registry name (pipeline reads state.cfg.alias)


@dataclass
class LocalState:
    cfg: LocalCfg
    hby: object
    hab: object
    rgy: object
    svc: ServiceAid


class LocalRuntime:
    """The in-wallet adapter wiring local providers + capture handlers.

    Caller owns lifecycle: close the LMDBLedger (idempotency) and stop the
    mailbox doer on unbind/vault-close — that teardown is the wallet plugin's
    job (Plan 2)."""

    def __init__(self, svc: ServiceAid, *, hby, hab, rgy,
                 idempotency=None, base_authz=None, verifier_tier="receipts"):
        self.svc = svc
        self.hby = hby
        self.hab = hab
        self.rgy = rgy

        if svc.verifier is None:
            svc.verifier = OracleVerifier(tier=verifier_tier)
        if svc.resolver is None:
            svc.resolver = BoundResolver(hab)
        if svc.issuer is None:
            svc.issuer = IpexGrantIssuer()
        if svc.deliverer is None:
            svc.deliverer = PostmanDeliverer()
        if svc.idempotency is None:
            svc.idempotency = idempotency or LMDBLedger(hby.db)
        if svc.authz is None:
            svc.authz = CredentialGate(hby=hby, reger=rgy.reger, svc=svc,
                                       base=Allowlist(base_authz or []))

        self.cred_verifier = verifying.Verifier(hby=hby, reger=rgy.reger)  # consumed by mailbox_doer() (Task 7) to admit IPEX-presented credentials

        self.state = LocalState(cfg=LocalCfg(alias=svc.alias),
                                hby=hby, hab=hab, rgy=rgy, svc=svc)

        self._captures: dict = {}
        for route in svc.routes:
            handler = _CaptureHandler(resource=route)
            self._captures[route] = handler
            hby.exc.addHandler(handler)

    def mailbox_doer(self, topics=None):
        """A MailboxDirector (a hio DoDoer) that polls the bound AID's witness
        mailbox, admits presented credentials (via self.cred_verifier), and routes
        command exns to hby.exc — where this runtime's capture handlers receive
        them. Mount it on the host Doist (the wallet does this via the plugin's
        get_doers()), then call process_captured() to drive the pipeline.

        NOTE: MailboxDirector polls ALL habs in the Habery, not only the bound
        hab; Plan 2 should scope polling to the bound AID (or accept wallet-wide
        polling). Captured command exns are still gated to the bound hab by the
        pipeline."""
        if topics is None:
            topics = ["/receipt", "/credential", "/reply"]
        return MailboxDirector(hby=self.hby, topics=topics,
                               verifier=self.cred_verifier, exc=self.hby.exc)

    def process_captured(self) -> None:
        """Drain every capture handler and drive the pipeline per verified exn.
        Each exn is guarded: a failure (e.g. resolver LookupError) is logged and
        suppressed so it cannot abort the drain or crash the host loop — matching
        the cloud handler's per-message contract."""
        for handler in self._captures.values():
            for serder, attachments in handler.drain():
                try:
                    pipeline.process(self.state, serder, attachments)
                except Exception:
                    logger.exception("pipeline error on %s (suppressed; drain continues)",
                                     getattr(serder, "said", "?"))
