"""The per-inbound compose: verify-tier → dispatch → idempotency → authorize →
compute → branch. Pure logic; every side effect goes through an injected provider
on `state.svc`. v1 ships GRANT on success + SILENCE on every other outcome
(deny / reject / none / unknown route / bad sig / compute-raise → log, no reply).

Ordering of exactly-once issuance + idempotent re-delivery is load-bearing:
record(said, grant) happens AFTER issue but BEFORE deliver, so a delivery failure
+ client re-send hits seen() and re-delivers the SAME grant (never re-issues)."""
from __future__ import annotations

import logging

from .contract import Request
from .providers.issue import Context
from .providers.verify import VerificationError

logger = logging.getLogger(__name__)


def process(state, serder, attachments) -> None:
    svc = state.svc
    sender = serder.ked["i"]
    route = serder.ked["r"]
    said = serder.said

    # 1. Verify the sender's assurance tier against the oracle key state.
    try:
        key_state = svc.verifier.verify(sender, attachments, state.hby)
    except VerificationError as exc:
        logger.warning("verification failed for %s on %s: %s — silent drop",
                       sender, route, exc)
        return

    # 2. Dispatch by the SIGNED `r`. No command → no behavior → no reply.
    cmd = svc.lookup(route)
    if cmd is None:
        logger.info("no command for route %s — silent drop", route)
        return

    # 3. Idempotency: a replay re-delivers the recorded grant, never re-issues.
    prior = svc.idempotency.seen(said)
    if prior is not None:
        endpoint = svc.resolver.resolve(sender, state.hby)
        ctx = Context(hby=state.hby, hab=state.hab, rgy=state.rgy,
                      registry_name=state.cfg.alias)
        svc.deliverer.deliver(prior, endpoint, ctx)
        logger.info("replay of %s — re-delivered recorded grant", said)
        return

    # 4. Authorize. v1 deny → log, no reply (signed spurn/denial is a follow-on).
    attrs = serder.ked.get("a", {}) or {}
    req = Request(sender=sender, route=route, payload=attrs, credentials=[],
                  message_said=said, key_state=key_state)
    allow, reason = svc.authz.authorize(req)
    if not allow:
        logger.info("authorization denied on %s: %s — silent drop", route, reason)
        return

    # 5. Compute. A raise → log, no reply, NOT recorded (safe re-send).
    try:
        reply = cmd.fn(req)
    except Exception:
        logger.exception("command %s raised — silent drop, not recorded", route)
        return

    # 6. Branch (v1 grant + silence).
    if reply.kind == "acdc":
        reply.schema_said = cmd.issues          # stamp the command's issued schema
        ctx = Context(hby=state.hby, hab=state.hab, rgy=state.rgy,
                      registry_name=state.cfg.alias)
        grant = svc.issuer.issue(reply, ctx)
        svc.idempotency.record(said, grant)     # BEFORE delivery (exactly-once issue)
        endpoint = svc.resolver.resolve(sender, state.hby)
        svc.deliverer.deliver(grant, endpoint, ctx)
        logger.info("issued + delivered grant for %s to %s", said, endpoint.eid)
        return

    if reply.kind == "revoke":
        ctx = Context(hby=state.hby, hab=state.hab, rgy=state.rgy,
                      registry_name=state.cfg.alias)
        notice = svc.issuer.revoke(reply, ctx)
        svc.idempotency.record(said, notice)    # BEFORE delivery (exactly-once revoke)
        endpoint = svc.resolver.resolve(sender, state.hby)
        svc.deliverer.deliver(notice, endpoint, ctx)
        logger.info("revoked + delivered notice for %s to %s", said, endpoint.eid)
        return

    if reply.kind == "publish":
        ctx = Context(hby=state.hby, hab=state.hab, rgy=state.rgy,
                      registry_name=state.cfg.alias)
        fs = svc.artifact_store.store(reply.artifact_said, reply.artifact_bytes,
                                      by=sender)
        # Merge first-seen provenance into the receipt attributes (dt is stamped
        # server-side by the issuer). priorContributor is null when first.
        reply.attributes = {**(reply.attributes or {}),
                            "firstSeen": fs.first_seen,
                            "priorContributor": (None if fs.first_seen
                                                 else {"aid": fs.first_publisher})}
        reply.schema_said = cmd.issues           # the publication_receipt schema
        grant = svc.issuer.issue(reply, ctx)     # mint + iss receipt into registry (ledger)
        svc.idempotency.record(said, grant)      # BEFORE delivery (exactly-once)
        if reply.want_receipt:
            endpoint = svc.resolver.resolve(sender, state.hby)
            svc.deliverer.deliver(grant, endpoint, ctx)
            logger.info("published %s + delivered receipt to %s",
                        reply.artifact_said, endpoint.eid)
        else:
            logger.info("published %s + recorded receipt (delivery not requested)",
                        reply.artifact_said)
        return

    # reject / none → v1: log, no reply.
    logger.info("command %s returned kind=%s — no reply (v1 grant+silence)",
                route, reply.kind)
