"""Signature-tolerant exn builder for the serviceaid tests.

keri's `exchanging.exchange()` has drifted across versions — the attributes/
recipient parameters have been spelled `attributes`/`receiver` and
`payload`/`recipient`. These tests only care about building a command exn, so
introspect the installed signature and use whichever kwargs exist. Keeps the
suite green against whatever keri the package is run against.
"""
import inspect

from keri.peer import exchanging


def make_exn(route, sender, recipient="", attributes=None):
    """Return an exn serder for `route` from `sender` (optionally to `recipient`)."""
    params = inspect.signature(exchanging.exchange).parameters
    kw = {"route": route, "sender": sender}
    kw["payload" if "payload" in params else "attributes"] = attributes if attributes is not None else {}
    if recipient:
        kw["recipient" if "recipient" in params else "receiver"] = recipient
    exn, _end = exchanging.exchange(**kw)
    return exn
