"""Issuer extension point + IpexGrantIssuer default.

Synchronous ACDC issuance + IPEX-grant framing for a Service-AID. Migrated from
the proven keri_cdk/handlers/serviceaid/issuing.py path (which mirrors the
Locksmith wallet credentialing/ipexing). v1 uses a no-backer registry so TEL
issuance needs no receipts and completes in-process on a virtual-time Doist.

WARNING (witnessed-AID limitation, carried over verbatim): the anchor ixn is
created INSIDE issue (and ensure_registry), so its witness receipts cannot be
pre-collected. For a WITNESSED service AID, Registrar.processWitnessEscrow holds
the tpwe escrow until ALL receipts arrive; on a virtual-time Doist that cannot
converge. v1 deploy uses a no-backer registry (effectively unwitnessed at the
TEL layer); completing witnessed TEL issuance is deferred work."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hio.base import doing

from keri.core import coring, eventing, serdering
from keri.core import signing as coresigning
from keri.help import helping
from keri.kering import Kinds
from keri.vdr import credentialing, verifying
from keri.app import grouping, signing
from keri.vc import protocoling

from ..contract import Reply


@dataclass
class Context:
    """Issuance/delivery handle threaded through the pipeline."""
    hby: object
    hab: object
    rgy: object
    registry_name: str


@runtime_checkable
class Issuer(Protocol):
    def issue(self, reply: Reply, ctx: Context) -> bytes:
        """Issue the ACDC declared by `reply` and return a signed IPEX grant exn."""
        ...


def ensure_registry(hby, hab, rgy, *, name: str):
    """Return the registry for `name`, creating it (no backers) if absent.
    The inception Custom Resource creates it exactly once at deploy time; this
    lazy create is a tests/bootstrap fallback (not race-safe)."""
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


class IpexGrantIssuer:
    """Default issuer: mints an ACDC of reply's schema to reply.recipient and
    returns a self-contained /ipex/grant exn (ACDC + iss + anchor)."""

    def issue(self, reply: Reply, ctx: Context) -> bytes:
        # The command's `issues` schema SAID was stamped onto the reply by the
        # pipeline (reply.attributes carry the data; reply.schema_said the schema).
        return self._issue_grant(
            ctx.hby, ctx.hab, ctx.rgy,
            schema_said=reply.schema_said, recipient=reply.recipient,
            attributes=reply.attributes, edges=reply.edges, rules=reply.rules,
            registry_name=ctx.registry_name)

    def _issue_grant(self, hby, hab, rgy, *, schema_said, recipient, attributes,
                     edges=None, rules=None, registry_name="svc",
                     message="", timestamp=None) -> bytearray:
        timestamp = timestamp or helping.nowIso8601()
        ensure_registry(hby, hab, rgy, name=registry_name)
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
        registry = rgy.registryByName(registry_name)
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
