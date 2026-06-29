"""Signature-tolerant exn builder for the serviceaid tests.

keri's `exchange()` has drifted across versions in BOTH location and signature:
  - it lives in `keri.core.eventing` in some trees and `keri.peer.exchanging` in
    others (older `peer.exchanging` only has `exchangeOld`);
  - its params have been spelled `attributes`/`receiver` and `payload`/`recipient`;
  - it returns either a bare serder or a `(serder, end)` tuple.
These tests only need to build a command exn, so resolve `exchange` from whichever
module exposes it and introspect the live signature. Keeps the suite green against
either keri tree (the fork's `keri/src` or an installed keri).
"""
import inspect


def _resolve_exchange():
    try:
        from keri.core import eventing
        fn = getattr(eventing, "exchange", None)
        if fn is not None:
            return fn
    except Exception:
        pass
    from keri.peer import exchanging
    return getattr(exchanging, "exchange", None) or exchanging.exchangeOld


_exchange = _resolve_exchange()


def make_exn(route, sender, recipient="", attributes=None):
    """Return an exn serder for `route` from `sender` (optionally to `recipient`)."""
    params = inspect.signature(_exchange).parameters
    kw = {"route": route, "sender": sender}
    kw["attributes" if "attributes" in params else "payload"] = attributes if attributes is not None else {}
    if recipient:
        kw["receiver" if "receiver" in params else "recipient"] = recipient
    res = _exchange(**kw)
    return res[0] if isinstance(res, tuple) else res
