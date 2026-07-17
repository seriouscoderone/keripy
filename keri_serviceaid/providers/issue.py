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
from keri.kering import Kinds, Vrsn_1_0
from keri.vdr import credentialing, verifying
from keri.app import grouping, signing
from keri.vc import protocoling

from ..contract import Reply
from ..progress import NullSink, ProgressSink


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


def _build_edge_source(edges: dict) -> dict:
    """Build the `e` block's per-edge source entries from edge defs.

    Each edge def carries `cred_said` (-> `n`) and `schema_said` (-> `s`).
    ACDC's default unary edge operator is I2I; when the def specifies a
    non-default `op` (e.g. NI2I for a self-issued/no-issuee edge target),
    that operator is surfaced as `o`. Absent `op`, `o` is omitted entirely
    so default I2I semantics apply implicitly per the ACDC spec."""
    source = {}
    for ename, edef in edges.items():
        entry = {"n": edef["cred_said"], "s": edef["schema_said"]}
        if "op" in edef:
            entry["o"] = edef["op"]
        source[ename] = entry
    return source


def issue_credential(hby, hab, rgy, *, schema_said, recipient, attributes,
                     registry_name, edges=None, rules=None, sink=None,
                     timestamp: str | None = None) -> str:
    """Ensure registry -> create -> anchor -> issue. Returns the credential SAID.

    Host-agnostic issue-only half of the mint+grant sequence (extracted from
    `IpexGrantIssuer._issue_grant`'s body): a Qt host (Locksmith) that owns
    its own peer-aware delivery calls this and `frame_grant_for` separately;
    `self_issue_and_grant` composes both for hosts that want one call.

    `timestamp`: when given (and `attributes` omits `dt`), it is stamped into
    the attribute block's `dt`, so it deterministically becomes both the
    ACDC's `dt` and the TEL iss event's `dt` — same timestamp, same SAID
    (retry/idempotency). Merely threading it into the `.get("dt", ...)`
    fallback below would NOT achieve this: `proving.credential` auto-stamps
    `dt` with its own now whenever absent, so that fallback never fires on
    this create path. When None, behavior is unchanged: `proving.credential`
    stamps its own now (fresh SAID per call).
    """
    sink = sink or NullSink()
    if timestamp is not None and attributes is not None and "dt" not in attributes:
        attributes = {**attributes, "dt": timestamp}
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
        source.update(_build_edge_source(edges))
        _, source = coring.Saider.saidify(sad=source, kind=Kinds.json,
                                          label=coring.Saids.d)
    # TRANSITIONAL: keripy v2 ACDC issuance is not implemented upstream
    # (vc/proving.py:79 hardcodes the v1 `ri` label; the v2 SerderACDC rejects
    # it). Hold the credential at v1 via the additive create(version=) seam.
    # Lift as a unit when upstream ships v2 registry+IPEX.
    creder = credentialer.create(regname=registry_name, recp=recipient,
                                 schema=schema_said, source=source,
                                 rules=rules, data=attributes, private=False,
                                 version=Vrsn_1_0)
    dt = creder.attrib.get("dt", timestamp)
    registry = rgy.registryByName(registry_name)
    iserder = registry.issue(said=creder.said, dt=dt)
    rseal = eventing.SealEvent(iserder.pre, iserder.snh, iserder.said)
    rseal = dict(i=rseal.i, s=rseal.s, d=rseal.d)
    # TRANSITIONAL (Task 7 finding): hab.interact/rotate default their `version`
    # kwarg to the module-level v2 constant regardless of the hab's OWN
    # established version, so a v1-inception'd hab (this file's v1-hold) was
    # silently anchoring with a v2-framed ixn/rot -- invisible to any
    # single-Habery test (the SAME hab's own Kever applies its own event
    # directly) but fatal to a cross-party recipient re-parsing this exact
    # event through an explicit-version Parser (as a real mailbox delivery,
    # or admit.py's embed re-parse, requires): "Incompatible message protocol
    # major version" / the event never lands (discovered building Task 7's
    # cross-party admit test). Inherit the hab's current established version
    # instead of the module default -- mirrors protocoling.py's
    # `serder.pvrsn if version is None else version` idiom -- so this is a
    # true no-op for a v2-inception'd hab and a real fix for the v1-held one.
    # Lift as a unit when upstream ships v2 registry+IPEX.
    if registry.estOnly:
        anc = hab.rotate(data=[rseal], version=hab.kever.serder.pvrsn)
    else:
        anc = hab.interact(data=[rseal], version=hab.kever.serder.pvrsn)
    aserder = serdering.SerderKERI(raw=bytes(anc))
    credentialer.issue(creder, iserder)
    registrar.issue(creder, iserder, aserder)
    _complete(rgy, registrar, iserder.pre, iserder.sn,
              verifier=verifier, credentialer=credentialer, cred_said=creder.said)
    sink.on_event("IssueCredentialDoer", "credential_issued",
                 {"said": creder.said, "schema_said": schema_said})
    return creder.said


def frame_grant_for(hby, hab, rgy, *, credential_said, recipient, sink=None,
                    message: str = "",
                    return_raw: bool = False) -> str | tuple[str, bytes]:
    """Frame the IPEX grant exn for an already-issued credential and parse it
    locally.

    Delivery is deliberately NOT included here (a `Deliverer`/host concern —
    Locksmith keeps its own peer-aware delivery; the CLI uses
    `PostmanDeliverer`). `_frame_grant` rebuilds the grant purely from
    already-persisted registry/KEL state (given just `credential_said`), so
    this function's only job beyond that is to parse the freshly-framed CESR
    stream locally to hand back its SAID (and, optionally, the raw bytes).

    `sink` is reserved for host progress emissions; unused today (hosts pass
    it uniformly alongside `issue_credential`'s).

    `message`: the human-readable IPEX message threaded verbatim into the
    grant exn's `a.m` field (see `protocoling.ipexGrantExn`'s `message`
    param / `exchanging.specialExchange`'s `attributes` handling). Defaults
    to `""` — byte-identical to before this kwarg existed. A host with a
    user-typed message (e.g. Locksmith's serviceaid bridge) passes it here
    so it survives the same way the legacy `SendGrantDoer` path preserved it.

    Returns the grant exn SAID (`str`) when `return_raw` is False (the
    default) — behavior byte-identical to before this kwarg existed. When
    `return_raw` is True, returns `(grant_exn_said, raw_bytes)`, where
    `raw_bytes` is the exact framed message (serder + attachments) parsed
    locally above — the deliverable a host's own peer-aware delivery
    (`Deliverer`) needs. Absent `return_raw`, that same payload is only
    recoverable after the fact via `exchanging.cloneMessage(hby, grant_said)`.
    """
    msg = _frame_grant(hby, hab, rgy, credential_said, recipient, message, helping.nowIso8601())
    serder = serdering.SerderKERI(raw=bytes(msg))
    if return_raw:
        return serder.said, bytes(msg)
    return serder.said


def self_issue_and_grant(hby, hab, rgy, *, schema_said, recipient, attributes,
                         registry_name, edges=None, rules=None, sink=None,
                         timestamp: str | None = None,
                         message: str = "",
                         return_raw: bool = False
                         ) -> tuple[str, str] | tuple[str, str, bytes]:
    """Compose `issue_credential` + `frame_grant_for`: issue the ACDC, then
    frame (and locally parse) its IPEX grant.

    `message`: forwarded verbatim to `frame_grant_for` (see its docstring) —
    defaults to `""`, unchanged from before this kwarg existed.

    Returns `(credential_said, grant_exn_said)` when `return_raw` is False
    (the default) — unchanged. When `return_raw` is True, returns
    `(credential_said, grant_exn_said, raw_bytes)`; `raw_bytes` is
    `frame_grant_for`'s `return_raw` payload — the exact framed grant
    message, passed through unchanged.

    This is the self-issuance path an onboarding "submit application" step
    uses (see `keri_serviceaid/egf/onboarding.py`'s `RequestPlan`): the
    requester self-issues their application credential and grants it to the
    selected authority. The CLI calls this directly, then hands the
    resulting grant off to its own `Deliverer`.
    """
    credential_said = issue_credential(
        hby, hab, rgy, schema_said=schema_said, recipient=recipient,
        attributes=attributes, registry_name=registry_name, edges=edges,
        rules=rules, sink=sink, timestamp=timestamp)
    result = frame_grant_for(hby, hab, rgy, credential_said=credential_said,
                             recipient=recipient, sink=sink, message=message,
                             return_raw=return_raw)
    if return_raw:
        grant_said, raw = result
        return credential_said, grant_said, raw
    return credential_said, result


def ensure_registry(hby, hab, rgy, *, name: str):
    """Return the registry for `name`, creating it (no backers) if absent.
    The inception Custom Resource creates it exactly once at deploy time; this
    lazy create is a tests/bootstrap fallback (not race-safe).

    Race mechanics: makeRegistry seeds a random nonce, so two cold starts that
    both miss the read branch would mint two DIFFERENT registries for the same
    name (last-write-wins in reger.regs). The deploy contract is that the
    inception CR creates the registry once, so the runtime path only ever hits
    the read branch above."""
    existing = rgy.registryByName(name)
    if existing is not None:
        return existing
    counselor = grouping.Counselor(hby=hby)
    registrar = credentialing.Registrar(hby=hby, rgy=rgy, counselor=counselor)
    registry = rgy.makeRegistry(name=name, prefix=hab.pre, noBackers=True,
                                nonce=coresigning.Salter().qb64)
    rseal = eventing.SealEvent(registry.regk, "0", registry.regd)
    rseal = dict(i=rseal.i, s=rseal.s, d=rseal.d)
    # TRANSITIONAL (Task 7 finding): see the matching comment in
    # `issue_credential` -- inherit the hab's established version rather than
    # the module-level v2 default.
    anc = hab.interact(data=[rseal], version=hab.kever.serder.pvrsn)
    aserder = serdering.SerderKERI(raw=bytes(anc))
    registrar.incept(iserder=registry.vcp, anc=aserder)
    _complete(rgy, registrar, registry.regk, 0)
    return registry


class IpexGrantIssuer:
    """Default issuer: mints an ACDC of reply's schema to reply.recipient and
    returns a self-contained /ipex/grant exn (ACDC + iss + anchor)."""

    def __init__(self, sink: ProgressSink | None = None):
        # sink=None -> NullSink: no behavior change from this class's callers'
        # point of view beyond the (no-op) sink calls below.
        self._sink = sink or NullSink()

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
        """Issue an ACDC of `schema_said` to `recipient`, return a CESR IPEX grant.

        Partial-failure semantics: if this dies after `hab.interact` but before
        `_complete`, the KEL keeps a harmless orphan anchor and the TEL iss event
        sits in escrow; the NEXT call's `_complete` pumps all escrows so the
        abandoned credential silently completes ("issued but never delivered").
        A retry mints a NEW credential SAID (fresh `dt`) UNLESS `attributes`
        carries an explicit `dt` (which reproduces the same SAID). This is WHY the
        pipeline records the request-SAID -> grant mapping (idempotency) BEFORE
        returning: a replay re-delivers the recorded grant rather than re-issuing.

        Thin delegate: mint+anchor+issue is `issue_credential`'s job (shared
        with the CLI/Locksmith paths); this method's own `message` knob is
        legacy the new host-agnostic functions don't need, so this reuses the
        shared `_frame_grant` helper directly (also used by `frame_grant_for`)
        rather than `self_issue_and_grant`, to avoid framing the grant twice
        under two different timestamps. An explicit `timestamp=` is passed
        through to `issue_credential` (stamped as the attribute `dt` when
        absent -> deterministic credential/TEL dt) and reused for the grant
        exn's dt; None (every current caller) leaves both to their own now,
        exactly as before.
        """
        credential_said = issue_credential(
            hby, hab, rgy, schema_said=schema_said, recipient=recipient,
            attributes=attributes, registry_name=registry_name, edges=edges,
            rules=rules, sink=self._sink, timestamp=timestamp)
        timestamp = timestamp or helping.nowIso8601()
        return _frame_grant(hby, hab, rgy, credential_said, recipient, message, timestamp)

    def revoke(self, reply: Reply, ctx: Context) -> bytearray:
        """Fire a TEL `rev` event for `reply.attributes["credential_said"]`,
        anchor it in the issuer's KEL, complete in-process (v1 = no-backer), and
        return a CESR notice carrying the revocation TEL event to the recipient.

        Mirrors the `issue` path: registry.revoke -> SealEvent -> hab.interact
        anchor -> Registrar.revoke -> _complete. Inherits the witnessed-AID
        limitation documented at module top: the anchor ixn is created here, so a
        WITNESSED registry's receipts cannot pre-converge on a virtual-time
        Doist. v1 deploy uses a no-backer registry.
        """
        return self._revoke_grant(
            ctx.hby, ctx.hab, ctx.rgy,
            said=reply.attributes["credential_said"], recipient=reply.recipient,
            registry_name=ctx.registry_name)

    def _revoke_grant(self, hby, hab, rgy, *, said, recipient,
                      registry_name="svc", message="", timestamp=None) -> bytearray:
        """Revoke the ACDC `said` and return a CESR notice framing the rev event.

        Partial-failure semantics mirror `_issue_grant`: a death after
        `hab.interact` but before `_complete` leaves a harmless orphan KEL anchor
        with the rev event in escrow; the next `_complete` pumps it through.
        """
        timestamp = timestamp or helping.nowIso8601()
        ensure_registry(hby, hab, rgy, name=registry_name)
        counselor = grouping.Counselor(hby=hby)
        registrar = credentialing.Registrar(hby=hby, rgy=rgy, counselor=counselor)
        registry = rgy.registryByName(registry_name)
        # registry.revoke(said, dt) -> SerderKERI of the rev/brv TEL event.
        rserder = registry.revoke(said=said, dt=timestamp)
        rseal = eventing.SealEvent(rserder.pre, rserder.snh, rserder.said)
        rseal = dict(i=rseal.i, s=rseal.s, d=rseal.d)
        # TRANSITIONAL (Task 7 finding): see the matching comment in
        # `issue_credential` -- inherit the hab's established version rather
        # than the module-level v2 default.
        if registry.estOnly:
            anc = hab.rotate(data=[rseal], version=hab.kever.serder.pvrsn)
        else:
            anc = hab.interact(data=[rseal], version=hab.kever.serder.pvrsn)
        aserder = serdering.SerderKERI(raw=bytes(anc))
        # Registrar.revoke(creder, rserder, anc): the real signature names the rev
        # event `rserder` (NOT `serder`); creder must be the SerderACDC (it reads
        # creder.regid), recovered via reger.cloneCred(said)[0].
        creder = rgy.reger.cloneCred(said=said)[0]
        registrar.revoke(creder=creder, rserder=rserder, anc=aserder)
        _complete(rgy, registrar, rserder.pre, rserder.sn)
        return _frame_revoke(hby, hab, rgy, said, recipient, message, timestamp)


def _frame_grant(hby, hab, rgy, said, recp, message, timestamp) -> bytearray:
    creder, prefixer, seqner, saider = rgy.reger.cloneCred(said=said)
    acdc = signing.serialize(creder, prefixer, seqner, saider)
    iss = rgy.reger.cloneTvtAt(creder.said)
    iserder = serdering.SerderKERI(raw=bytes(iss))
    sq = coring.Seqner(sn=iserder.sn)
    serder = hby.db.fetchLastSealingEventByEventSeal(
        creder.sad["i"], seal=dict(i=iserder.pre, s=sq.snh, d=iserder.said))
    anc = hby.db.cloneEvtMsg(pre=serder.pre, fn=0, dig=serder.said)
    # TRANSITIONAL v1 hold: pin the grant exn to v1 (pvrsn) so it stays JSON with
    # v1 embed framing (v2 CESR-native forbids pathed embeds). Lift with v2 IPEX.
    exn, atc = protocoling.ipexGrantExn(hab=hab, recp=recp, message=message,
                                        acdc=acdc, iss=iss, anc=anc, dt=timestamp,
                                        pvrsn=Vrsn_1_0)
    msg = bytearray(exn.raw)
    msg.extend(atc)
    return msg


def _frame_revoke(hby, hab, rgy, said, recp, message, timestamp) -> bytearray:
    """Frame the latest revocation TEL event into an IPEX grant-style notice the
    holder observes, mirroring `_frame_grant`.

    Deviation from issue: `cloneTvtAt(said, sn=0)` is the `iss` event; the `rev`
    event for a single-issuance credential is at TEL sn=1, so we clone sn=1 and
    pass it as `iss` (the grant exn's TEL-event embed slot). The anchoring KEL
    event is the issuer's LATEST sealing event for the rev seal (not the
    issuance one), recovered via fetchLastSealingEventByEventSeal."""
    creder, prefixer, seqner, saider = rgy.reger.cloneCred(said=said)
    acdc = signing.serialize(creder, prefixer, seqner, saider)
    rev = rgy.reger.cloneTvtAt(creder.said, sn=1)
    rserder = serdering.SerderKERI(raw=bytes(rev))
    sq = coring.Seqner(sn=rserder.sn)
    serder = hby.db.fetchLastSealingEventByEventSeal(
        creder.sad["i"], seal=dict(i=rserder.pre, s=sq.snh, d=rserder.said))
    anc = hby.db.cloneEvtMsg(pre=serder.pre, fn=0, dig=serder.said)
    # TRANSITIONAL v1 hold: pin the grant exn to v1 (pvrsn), matching _frame_grant.
    exn, atc = protocoling.ipexGrantExn(hab=hab, recp=recp, message=message,
                                        acdc=acdc, iss=rev, anc=anc, dt=timestamp,
                                        pvrsn=Vrsn_1_0)
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
