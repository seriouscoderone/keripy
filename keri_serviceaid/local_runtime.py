"""LocalRuntime — the in-wallet adapter.

Drives the keri_serviceaid pipeline against a wallet's LMDB Habery and ONE bound
AID (hab) + its registry (rgy). Wires the local-variant providers, registers a
capture handler per command route on hby.exc, and drains them through
pipeline.process.

Inbound transport is the HOST's responsibility, not the library's. A Service-AID
is an application that a KERI node hosts — it is not itself a node, so it never
constructs its own mailbox poller. The host injects one (e.g. the wallet's
MailboxDirector / vault.mbx) that feeds hby.exc (where this runtime registers its
capture handlers) and polls `command_topics`. For tests/headless use, feed exns
to the exchanger directly and call process_captured().

No DynamoDB, no Qt — pure keripy."""
from __future__ import annotations

import logging
from dataclasses import dataclass

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

    Inbound transport is injected by the host: it mounts a mailbox poller that
    feeds hby.exc (where this runtime registers its capture handlers) and polls
    `command_topics`; the runtime never constructs one (a Service-AID is hosted
    by a node, it is not a node). Caller owns lifecycle: close the LMDBLedger
    (idempotency) and stop the injected poller on unbind/vault-close."""

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

        self.cred_verifier = verifying.Verifier(hby=hby, reger=rgy.reger)  # the host wires this into its injected poller to admit IPEX-presented credentials

        self.state = LocalState(cfg=LocalCfg(alias=svc.alias),
                                hby=hby, hab=hab, rgy=rgy, svc=svc)

        self._captures: dict = {}
        for route in svc.routes:
            handler = _CaptureHandler(resource=route)
            self._captures[route] = handler
            hby.exc.addHandler(handler)

    @property
    def command_topics(self) -> list[str]:
        """Mailbox topics the host's injected poller must poll to receive this
        Service-AID's command exns.

        A command exn is forwarded under the first segment of its route (the
        keripy Postman default: ``route.strip("/").split("/")[0]`` — e.g. a
        ``/insurance/cmd/grant_license`` command arrives under topic
        ``"insurance"``). The host adds these to whatever standard topics it
        already polls (``/receipt``, ``/credential``, ...). The runtime exposes
        them; the host owns the poller (dependency injection)."""
        return sorted({route.strip("/").split("/")[0]
                       for route in self.svc.routes if route.strip("/")})

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
