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


def signing_hab(hby, preferred_alias: str | None = None):
    """The hab a sync query must be signed with, or None if there is none.

    MUST be TRANSFERABLE, and that is a protocol requirement rather than a
    preference. A non-transferable signer attaches NonTransReceiptCouples
    (cigars) instead of an indexed-signature group, and keripy's
    `parsing.msgProcess` then evaluates `exts['source']` at
    `parsing.py:1526` — a key `MsgParseDom` does not declare, populated only
    on the `lsgs` branch. The query dies with `KeyError: 'source'` BEFORE
    `Kevery.processQuery` is reached, so nothing replays and nothing is
    logged at INFO. (The sibling logic in `eventing.py:4672` uses
    `.get('source')` and is correct; `parsing.py` is the drifted twin.)

    Prefers `preferred_alias` so the query is attributable to the user's own
    identifier rather than to whichever hab happens to be first in the dict
    — `hby.habs` also holds infrastructure EIDs such as locksmith's
    non-transferable "peer-listener".
    """
    if preferred_alias:
        hab = hby.habByName(preferred_alias)
        if hab is not None and hab.kever.prefixer.transferable:
            return hab
    for hab in hby.habs.values():
        if hab.kever.prefixer.transferable:
            return hab
    return None


def kel_sync_request(hab, peer_pre: str) -> bytes:
    """Signed `qry` bytes asking `peer_pre` to replay ITS OWN KEL.

    `pre` and `src` are both the peer: we are asking that identifier, about
    that identifier. `Hab.query` signs with the asking hab, so the answering
    side can attribute the request.

    The protocol version is pinned to the SIGNING HAB'S OWN KEL version
    rather than left to default. `eventing.query`'s `version` parameter
    defaults to the module-level `Version` — currently v2 — regardless of
    what the hab or the deployment actually speaks, so the default mints a
    v2 body even for a v1 identifier. A parser whose stream genus is v1
    rejects that at `serdering.py:196` with `DeserializeError: Incompatible
    message protocol major version=2 with stream genus major version=1`,
    which `groupParsator` re-wraps at `parsing.py:910` as a BARE
    `ExtractionError` — hence the message-less "Parser msg extraction
    error:" and a query dropped before it is ever processed.

    Deriving from `hab` instead of hardcoding a version keeps this module
    protocol-neutral: a v1 identifier asks in v1, a v2 identifier asks in
    v2, and no consumer inherits another consumer's transitional pin.
    """
    vrsn = hab.kever.serder.pvrsn
    return bytes(hab.query(pre=peer_pre, src=peer_pre, route=LOGS_ROUTE,
                           pvrsn=vrsn, gvrsn=vrsn))


def body_request(hab, said: str, *, peer_pre: str | None = None) -> bytes:
    """Signed `pro` bytes asking for the body behind `said`.

    Delegates to Plan A's `ProdClient` rather than re-deriving the message, so
    there is one construction of a prod in the codebase. `peer_pre` is a
    routing selector only — a `pro` carries no recipient, and which peer
    receives it is decided by delivery (see ProdClient.request's docstring).

    DELIBERATELY takes no version argument, unlike `kel_sync_request` above.
    `ProdClient.request` already defaults `pvrsn=Vrsn_1_0` explicitly
    (`prodding.py:210`, with a docstring saying so), so a `pro` is minted v1
    and needs no correction — measured against the live wire, where every
    logged extraction error paired with a `qry` and never once with a `pro`.
    Adding a version pin here would be a fix for a bug this path does not
    have.
    """
    return bytes(ProdClient(hab).request(pre=peer_pre or hab.pre, said=said,
                                         route=SEALED_ROUTE,
                                         anchorPre=peer_pre))


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


def ingest_response(hby, raw, *, verifier=None, exc=None, version=None) -> int:
    """Parse a peer's reply into our own dbs. Returns events newly accepted.

    THE OTHER HALF OF ASKING. locksmith's peer transport is push-only —
    `peer_send` opens a connection, writes, and closes it — so a responder's
    reply lands in a socket the asker has already hung up on. Measured on a
    live deployment: the responder logged "Server peer-listener: sent chit or
    receipt or replay: 459" every 5s while the asking side's registry held
    zero bodies and re-requested the same SAIDs forever. The queries were
    answered; nobody was listening.

    Mirrors the Reactant's processing stack (locksmith
    turret/directing.py:536-575) rather than inventing one, so a reply read
    off a socket is processed exactly as a reply pushed to a listener would
    be:

      * `lax=False, local=False` — a peer's KEL is strictly verified.
      * `tvy` from the verifier's reger, else streamed registry TEL events
        (vcp/iss) are dropped with "No tevery to process".
      * `vry=verifier` — else a bare ACDC has nowhere to route and is dropped
        with "No verifier to process so dropped ACDC=...".
      * reply routes registered on the Revery, else `rpy` is unroutable.

    `version` defaults to the version of the reply itself. A parser pinned to
    the wrong genus drops every message and raises NOTHING — the failure this
    module's own history is made of.
    """
    from keri import kering
    from keri.core import eventing, parsing, routing
    from keri.vdr.eventing import Tevery

    if not raw:
        return 0

    if version is None:
        version = (kering.smell(raw).pvrsn if kering.sniff(raw) == kering.Colds.msg
                   else kering.Vrsn_2_0)

    rvy = routing.Revery(db=hby.db)
    kvy = eventing.Kevery(db=hby.db, lax=False, local=False, rvy=rvy)
    kvy.registerReplyRoutes(router=rvy.rtr)

    tvy = None
    if verifier is not None:
        tvy = Tevery(reger=verifier.reger, db=hby.db, local=False, rvy=rvy)
        tvy.registerReplyRoutes(router=rvy.rtr)

    # Count EVENTS, not identifiers. len(hby.kevers) counts how many AIDs we
    # know, which goes to zero delta the moment a peer is known at all — so a
    # watch reporting on it says "0" forever while the very KEL growth it
    # exists to observe streams past. What a watch cares about is a peer's KEL
    # getting LONGER: that is where the next anchor lives.
    def _height():
        return sum(kev.sn + 1 for kev in hby.kevers.values())

    before = _height()
    parsing.Parser(kvy=kvy, tvy=tvy, rvy=rvy, vry=verifier, exc=exc,
                   framed=True, version=version).parse(
                       ims=bytearray(raw), kvy=kvy, tvy=tvy, rvy=rvy, vry=verifier)
    kvy.processEscrows()
    if tvy is not None:
        tvy.processEscrows()
    return _height() - before
