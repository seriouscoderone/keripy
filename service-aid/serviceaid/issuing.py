"""Synchronous ACDC issuance + IPEX-grant framing for a Service AID.

Adapted from the proven synchronous path in the Locksmith wallet
(credentialing.py / ipexing.py), which runs against this keripy fork.
v1 uses a no-backer registry so TEL issuance needs no receipts and completes
in-process.

WARNING -- witnessed-AID limitation: the anchor ixn is created INSIDE
`issue_grant` (and `ensure_registry`), so its witness receipts cannot be
pre-collected by the caller. For a WITNESSED service AID,
`Registrar.processWitnessEscrow` (keri/vdr/credentialing.py:740-760) holds
the tpwe escrow until ALL witness receipts arrive, so `_complete` on a
virtual-time Doist will exhaust its rounds and raise. The runtime (Task 7)
must either run completion on a real-time Doist with a deadline or keep the
issuance path effectively unwitnessed at the TEL layer.
"""
from __future__ import annotations

from hio.base import doing

from keri.core import coring, eventing, serdering
from keri.core import signing as coresigning
from keri.help import helping
from keri.kering import Kinds
from keri.vdr import credentialing, verifying
from keri.app import grouping, signing
from keri.vc import protocoling


def ensure_registry(hby, hab, rgy, *, name: str):
    """Return the credential registry for `name`, creating it (no backers) if absent.

    Concurrency contract: creation is NOT race-safe. `makeRegistry` is seeded
    with a random nonce, so two racing cold starts that both miss the read
    branch would mint two different registries for the same name
    (last-write-wins in `reger.regs`). The deployment contract is that the
    inception Custom Resource (Task 11) creates the registry exactly once at
    deploy time, and the runtime path only ever hits the read branch here.
    Lazy creation below is a fallback for tests/bootstrap, not the design.
    """
    existing = rgy.registryByName(name)
    if existing is not None:
        return existing

    counselor = grouping.Counselor(hby=hby)
    registrar = credentialing.Registrar(hby=hby, rgy=rgy, counselor=counselor)

    registry = rgy.makeRegistry(name=name, prefix=hab.pre, noBackers=True,
                                nonce=coresigning.Salter().qb64)
    rseal = eventing.SealEvent(registry.regk, "0", registry.regd)
    rseal = dict(i=rseal.i, s=rseal.s, d=rseal.d)
    anc = hab.interact(data=[rseal])
    aserder = serdering.SerderKERI(raw=bytes(anc))
    registrar.incept(iserder=registry.vcp, anc=aserder)
    _complete(rgy, registrar, registry.regk, 0)
    return registry


def issue_grant(hby, hab, rgy, *, schema_said: str, recipient: str,
                attributes: dict, edges: dict | None = None,
                rules: dict | None = None, registry_name: str = "svc",
                message: str = "", timestamp: str | None = None) -> bytearray:
    """Issue an ACDC of `schema_said` to `recipient` and return a CESR IPEX grant.

    Partial-failure semantics: if this dies after `hab.interact` but before
    completion, the KEL keeps a harmless orphan anchor and the TEL iss event
    sits in escrow. The NEXT `issue_grant` call's `_complete` pumps ALL
    escrows, so the abandoned credential silently completes -- "issued but
    never delivered". A retry mints a NEW credential SAID (a fresh `dt` is
    injected by the credential factory) UNLESS the caller passes `attributes`
    containing an explicit `dt`, in which case the same SAID is reproduced.
    Callers (handler/idempotency layers) must record the
    request-SAID -> grant mapping BEFORE returning the reply.
    """
    timestamp = timestamp or helping.nowIso8601()
    registry = ensure_registry(hby, hab, rgy, name=registry_name)

    counselor = grouping.Counselor(hby=hby)
    registrar = credentialing.Registrar(hby=hby, rgy=rgy, counselor=counselor)
    verifier = verifying.Verifier(hby=hby, reger=rgy.reger)
    credentialer = credentialing.Credentialer(hby=hby, rgy=rgy,
                                              registrar=registrar, verifier=verifier)

    source = None
    if edges:
        source = dict(d="")
        for ename, edef in edges.items():
            source[ename] = {"n": edef["cred_said"], "s": edef["schema_said"]}
        _, source = coring.Saider.saidify(sad=source, kind=Kinds.json,
                                          label=coring.Saids.d)

    creder = credentialer.create(regname=registry_name, recp=recipient,
                                 schema=schema_said, source=source,
                                 rules=rules, data=attributes, private=False)

    dt = creder.attrib.get("dt", timestamp)
    iserder = registry.issue(said=creder.said, dt=dt)
    rseal = eventing.SealEvent(iserder.pre, iserder.snh, iserder.said)
    rseal = dict(i=rseal.i, s=rseal.s, d=rseal.d)
    if registry.estOnly:
        anc = hab.rotate(data=[rseal])
    else:
        anc = hab.interact(data=[rseal])
    aserder = serdering.SerderKERI(raw=bytes(anc))
    credentialer.issue(creder, iserder)
    registrar.issue(creder, iserder, aserder)
    _complete(rgy, registrar, iserder.pre, iserder.sn,
              verifier=verifier, credentialer=credentialer, cred_said=creder.said)

    return _frame_grant(hby, hab, rgy, creder.said, recipient, message, timestamp)


def _frame_grant(hby, hab, rgy, said, recp, message, timestamp) -> bytearray:
    """Build a self-contained IPEX /ipex/grant exn carrying ACDC + iss + anchor."""
    creder, prefixer, seqner, saider = rgy.reger.cloneCred(said=said)
    acdc = signing.serialize(creder, prefixer, seqner, saider)

    iss = rgy.reger.cloneTvtAt(creder.said)
    iserder = serdering.SerderKERI(raw=bytes(iss))
    sq = coring.Seqner(sn=iserder.sn)
    serder = hby.db.fetchLastSealingEventByEventSeal(
        creder.sad["i"], seal=dict(i=iserder.pre, s=sq.snh, d=iserder.said))
    anc = hby.db.cloneEvtMsg(pre=serder.pre, fn=0, dig=serder.said)

    exn, atc = protocoling.ipexGrantExn(hab=hab, recp=recp, message=message,
                                        acdc=acdc, iss=iss, anc=anc, dt=timestamp)
    msg = bytearray(exn.raw)
    msg.extend(atc)
    return msg


def _complete(rgy, registrar, pre, sn, *, verifier=None, credentialer=None,
              cred_said=None, rounds: int = 64):
    """Drive escrow processing until the TEL event at (pre, sn) is complete.

    `registrar.complete` requires both the committed TEL event (ctel) and the
    WitnessPublisher having "sent" the event, and the latter is only recorded
    by the witness-publisher Doer loop (a no-op send for a no-backer registry
    on an unwitnessed AID). So the Registrar (and Credentialer, during
    issuance) Doers are run synchronously on a virtual-time Doist while the
    Regery/Verifier escrows are pumped in between. For a no-backer registry
    this converges in a few recurs; the bounded loop is a safety net rather
    than a wait on network I/O.
    """
    def _done():
        if not registrar.complete(pre=pre, sn=sn):
            return False
        if credentialer is not None and cred_said is not None:
            return credentialer.complete(said=cred_said)
        return True

    doers = [registrar] if credentialer is None else [registrar, credentialer]
    doist = doing.Doist(real=False, tock=1.0)
    deeds = None
    try:
        deeds = doist.enter(doers=doers)
        for _ in range(rounds):
            if _done():
                return
            rgy.processEscrows()
            if verifier is not None:
                verifier.processEscrows()
            doist.recur(deeds=deeds)
        if not _done():
            raise RuntimeError(f"TEL event (pre={pre}, sn={sn}) did not complete")
    finally:
        if deeds is not None:
            doist.exit(deeds=deeds)
