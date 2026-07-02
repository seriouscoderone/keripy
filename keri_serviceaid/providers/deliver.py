"""Deliverer extension point + PostmanDeliverer default.

A reply is a NEW signed message routed to the requester's mailbox — never the
HTTP response. PostmanDeliverer wraps forwarding.Poster, which envelopes the
grant in a /fwd exn and posts it to the resolved endpoint provider (mailbox /
controller / agent / witness) for store-and-forward. The requester polls its
mailbox (SSE qry route='mbx') to receive it."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from keri.core import serdering
from keri.app import forwarding

from .resolve import Endpoint
from .issue import Context

GRANT_TOPIC = "credential"


@runtime_checkable
class Deliverer(Protocol):
    def deliver(self, msg: bytes, endpoint: Endpoint, ctx: Context) -> None:
        """Deliver the signed grant `msg` to `endpoint` (async, store-and-forward)."""
        ...


class PostmanDeliverer:
    """Default deliverer. Splits the grant CESR stream into serder + attachment,
    enqueues it on a Poster targeting endpoint.eid, then drains the Poster on a
    virtual-time Doist so the /fwd post completes within the Lambda invocation."""

    def __init__(self, poster=None):
        self._poster = poster   # injectable for tests; None ⇒ build per-deliver

    def deliver(self, msg: bytes, endpoint: Endpoint, ctx: Context) -> None:
        ims = bytearray(msg)
        serder = serdering.SerderKERI(raw=bytes(ims))
        del ims[:serder.size]
        attachment = bytes(ims) if ims else None

        # dest is the RECIPIENT (endpoint.cid), not the resolved provider (endpoint.eid):
        # Poster.forward() re-resolves the recipient's mailbox and /fwd-posts the grant
        # there (stored under {recipient}/credential). Passing the provider eid instead
        # makes Poster take the provider's own controller endpoint and bare-POST the grant,
        # which a serverless mailbox drops. Fall back to eid for endpoints built without cid.
        dest = endpoint.cid or endpoint.eid
        poster = self._poster or forwarding.Poster(hby=ctx.hby)
        poster.send(dest=dest, topic=GRANT_TOPIC, serder=serder,
                    hab=ctx.hab, attachment=attachment)

        if self._poster is None:
            # Drive the real Poster's deliverDo to completion (it queues then posts).
            # NOTE: Doist.do(doers=...) replaces self.doers, so the doer is entered
            # exactly once here — do NOT also pass doers= to the constructor.
            from hio.base import doing
            doist = doing.Doist(real=False, tock=0.03125, limit=8.0)
            doist.do(doers=[poster])
