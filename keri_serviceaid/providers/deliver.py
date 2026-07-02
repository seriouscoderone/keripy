"""Deliverer extension point + PostmanDeliverer default.

A reply is a NEW signed message routed to the requester's mailbox — never the
HTTP response. PostmanDeliverer wraps forwarding.Poster, which envelopes the
grant in a /fwd exn and posts it to the resolved endpoint provider (mailbox /
controller / agent / witness) for store-and-forward. The requester polls its
mailbox (SSE qry route='mbx') to receive it."""
from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from hio.base import doing
from keri.core import serdering
from keri.app import forwarding

from .resolve import Endpoint
from .issue import Context

GRANT_TOPIC = "credential"

# The Poster's /fwd POST is real network I/O: the TLS connect + response to a remote
# mailbox takes wall-clock seconds. The drive must therefore be REAL-paced (advance in
# step with wall-clock, sleeping between recurs so the socket can progress) and exit as
# soon as the send completes — never a virtual-time Doist that races to its limit in
# milliseconds and abandons the connect (the "give up before connect" bug class).
_DELIVER_TIMEOUT_S = 15.0
_DRIVE_TOCK = 0.03125


def _drive_until_sent(doist, deeds, poster, said, *, timeout=_DELIVER_TIMEOUT_S,
                      sleep=time.sleep, now=time.monotonic):
    """Recur `doist` over `deeds`, real-paced, until `poster.sent(said)` or `timeout`
    wall-clock seconds elapse. `sleep`/`now` are injectable for tests."""
    start = now()
    while not poster.sent(said) and (now() - start) < timeout:
        doist.recur(deeds=deeds)
        sleep(doist.tock)


@runtime_checkable
class Deliverer(Protocol):
    def deliver(self, msg: bytes, endpoint: Endpoint, ctx: Context) -> None:
        """Deliver the signed grant `msg` to `endpoint` (async, store-and-forward)."""
        ...


class PostmanDeliverer:
    """Default deliverer. Splits the grant CESR stream into serder + attachment,
    enqueues it on a Poster targeting the recipient (endpoint.cid), then drives the
    Poster on a real-paced Doist until the /fwd POST completes (or a wall-clock cap)."""

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
            # Drive the real Poster's deliverDo until the /fwd POST completes. Real-paced
            # with early exit (see _drive_until_sent): a virtual-time Doist would blow
            # through its limit before the live TLS connect finished and drop the POST.
            doist = doing.Doist(real=True, tock=_DRIVE_TOCK)
            deeds = doist.enter(doers=[poster])
            try:
                _drive_until_sent(doist, deeds, poster, serder.said)
            finally:
                doist.exit(deeds=deeds)
