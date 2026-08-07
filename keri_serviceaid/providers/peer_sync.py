# -*- encoding: utf-8 -*-
"""Asking a peer for what a watch needs: KEL updates, then sealed bodies.

`AnchorWatcher` (Plan A) reads anchors out of a KEL the wallet ALREADY HAS, and
`sealed_retrieval` (C1) verifies a body the wallet ALREADY HAS. Neither fetches.
In a witnessed deployment that gap is filled by witnesses; a witness-less
direct-mode peer has nobody to ask on its behalf, so the watching side has to
ask the watched side itself. Without this, "the actuary sees the mandate by
watching the CUO's log" is true of the verification and false of the delivery:
the watcher runs against a KEL frozen at whatever the pairing handshake left
behind, and never observes another anchor.

BOTH halves already exist on the answering side, which is why this module is
small:

  * `qry` route "logs" -> `Kevery.processQuery` replays the KEL
    (keripy eventing.py; locksmith's direct-mode Reactant builds its Parser
    with `kvy=`, and its reply path is literally labelled "chit or receipt or
    replay").
  * `pro` route "/sealed" -> `ProdResponder` answers with a `bar` carrying the
    body (Plan A), and `ProdClient.harvest` unpacks it.

PURE ON PURPOSE — no transport, no sockets, no doers. Every function here
either reads the dbs handed to it or returns signed bytes for a caller to
deliver however it delivers things. That is the same posture as `credgate`'s
`holds_credential(reger, sender, req)` and `sealed_retrieval`'s
`credential_said_from_seal(seal, iss_event)`, and it is what lets the GUI, a
CLI and a service share one implementation instead of three.
"""
from __future__ import annotations

from keri.app.prodding import ProdClient

#: The KEL-replay query route. `Kevery.processQuery` dispatches on the bare
#: string "logs" (not "/logs") — see its `if route == "logs":` branch.
LOGS_ROUTE = "logs"

#: The seal-oriented retrieval route Plan A's ProdResponder answers on.
SEALED_ROUTE = "/sealed"


def kel_sync_request(hab, peer_pre: str) -> bytes:
    """Signed `qry` bytes asking `peer_pre` to replay ITS OWN KEL.

    `pre` and `src` are both the peer: we are asking that identifier, about
    that identifier. `Hab.query` signs with the asking hab, so the answering
    side can attribute the request.
    """
    return bytes(hab.query(pre=peer_pre, src=peer_pre, route=LOGS_ROUTE))


def body_request(hab, said: str, *, peer_pre: str | None = None) -> bytes:
    """Signed `pro` bytes asking for the body behind `said`.

    Delegates to Plan A's `ProdClient` rather than re-deriving the message, so
    there is one construction of a prod in the codebase. `peer_pre` is a
    routing selector only — a `pro` carries no recipient, and which peer
    receives it is decided by delivery (see ProdClient.request's docstring).
    """
    return bytes(ProdClient(hab).request(pre=peer_pre or hab.pre, said=said,
                                         route=SEALED_ROUTE))


def missing_bodies(reger, saids) -> list[str]:
    """Of `saids`, those whose credential body is NOT already local.

    Pure filter over the registry, so a caller can ask for exactly what it
    lacks instead of re-requesting on every tick. A SAID whose body is present
    needs no `pro`; the watch's verification path takes it from there.
    """
    out = []
    for said in saids:
        if not said:
            continue
        try:
            if reger.creds.get(keys=(said,)) is None:
                out.append(said)
        except Exception:  # noqa: BLE001 — an unreadable reger means "unknown"
            out.append(said)
    return out


def anchored_saids(watcher, since=None) -> list[str]:
    """SAIDs anchored in a watched KEL at or after `since`.

    Thin, deliberately: `AnchorWatcher.since()` yields `(sn, seal)` and the
    seal's `i` is the anchored SAID. Wrapping it here keeps callers from
    reaching into seal internals in three places, and keeps this module the
    one thing that knows what a watch produces.
    """
    found = []
    for _sn, seal in watcher.since(since if since is not None else watcher.checkpoint):
        said = (seal or {}).get("i")
        if said:
            found.append(said)
    return found
