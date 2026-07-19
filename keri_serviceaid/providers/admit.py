"""IPEX admit — single-sig lift from Locksmith.

Lifted from `locksmith.core.ipexing` (locksmith commit b5de74a, "make IPEX
admit version-agnostic"): `_embed_serder` and the `Admitter.admit` method
body, adapted from a stateful class into this module's stand-alone
`admit_grant` function. Closes serviceaid's remaining IPEX gap — it could
issue and grant but not ADMIT an inbound grant.

Two real bugs the lift source fixed, preserved here:

1. keripy's `ipexGrantExn` no longer emits a `reg` (registry inception TEL
   event) embed — the receiver reconstructs TEL/registry state from `anc`
   (the anchoring KEL event) + `iss` (the credential's own TEL issuance
   event) alone. The old code iterated a hard-coded ("anc", "reg", "iss",
   "acdc") tuple and hard-indexed `embeds[label]`, so every real grant raised
   `KeyError: 'reg'` before any credential parsed.
2. `coring.Sadder` serializes only the library's current protocol version
   (v2, via `sizeify`), so it raises `Unsupported version` on the v1 events
   the v1-hold still emits. `_embed_serder` selects the concrete Serder by
   embed label instead — each Serder reads the protocol version from the
   ked's own version string, so it handles v1 today and v2 later unchanged.

Single-sig only: multisig admit (GroupHab coordination, the
`grouping.multisigExn`/signing-member fan-out dance) stays in Locksmith
until its own lift item — this module has no GroupHab branch at all.

DRY with keripy: stock primitives only (`exchanging`, `parsing`,
`protocoling`) — no locksmith imports. Locksmith's `core.remoting.
message_version` helper (used by the lift source to pick a per-message CESR
version) is reproduced locally as `_message_version` from the same stock
`kering.sniff`/`kering.smell` calls it wraps, so no cross-repo import is
needed.
"""
from __future__ import annotations

from keri import kering
from keri.app.notifying import Notifier
from keri.core import eventing, parsing, serdering
from keri.help import helping
from keri.peer import exchanging
from keri.vc import protocoling
from keri.vdr import eventing as teventing, verifying

from ..progress import NullSink, ProgressSink


def _message_version(ims: bytes | bytearray) -> kering.Versionage:
    """Stock-keripy equivalent of locksmith.core.remoting.message_version:
    the KERI wire version for a complete raw message. Kept dependency-free
    of locksmith (DRY with keripy — stock primitives only)."""
    if kering.sniff(ims) == kering.Colds.msg:
        return kering.smell(ims).pvrsn
    return kering.Vrsn_2_0


def _embed_serder(label, ked):
    """Version-agnostic re-serialization of a grant embed.

    ``coring.Sadder`` serializes only the library's current protocol version
    (v2 in keri 2.0.0-dev6, via ``sizeify``), so it raises
    ``Unsupported version`` on the v1 events the wallet still emits during the
    KERI v2 v1-hold. Selecting the concrete Serder by embed label instead keeps
    this version-agnostic: each Serder reads the protocol version from the ked's
    own version string, so it handles v1 today and v2 later unchanged.

    Parameters:
        label (str): grant embed label ("acdc", "anc", or "iss").
        ked (dict): the embedded key/credential event dict.

    Returns:
        Serder: SerderACDC for the ``acdc`` embed, SerderKERI otherwise.
    """
    if label == "acdc":
        return serdering.SerderACDC(sad=ked)
    return serdering.SerderKERI(sad=ked)


def _default_exchanger(hby):
    """Build a stand-alone Exchanger with IPEX handlers loaded (mirrors
    `Admitter.__init__`'s fallback branch, minus the multisig-only
    `grouping.loadHandlers`/`Multiplexor` wiring this single-sig-only lift
    does not need — `protocoling.loadHandlers` alone registers `/ipex/admit`
    and every other IPEX route)."""
    notifier = Notifier(hby)
    exc = exchanging.Exchanger(hby=hby, handlers=[])
    protocoling.loadHandlers(hby, exc=exc, notifier=notifier)
    return exc


def admit_grant(hby, hab, rgy, *, grant_said: str, exc=None, kvy=None,
                tvy=None, vry=None, message: str = "",
                sink: ProgressSink | None = None) -> str:
    """Admit the inbound `/ipex/grant` exn `grant_said`.

    Clones the grant exn from `hby`'s local store (as `exchanging.
    cloneMessage` would after a real mailbox delivery parsed it in), parses
    its embeds version-agnostically to verify + save the credential, then
    frames and locally parses the admit exn. Returns the admit exn's SAID.

    Single-sig only: raises `NotImplementedError` if `hab` is a GroupHab —
    multisig admit stays in Locksmith until the lift item.
    """
    if hab.__class__.__name__ == "GroupHab":
        raise NotImplementedError(
            "multisig admit stays in Locksmith until the lift item")

    sink = sink or NullSink()
    exc = exc if exc is not None else _default_exchanger(hby)

    # Default the embed processors when the caller passes none. The lift
    # source (`locksmith.core.ipexing.Admitter.__init__`) built these three
    # store-bound processors whenever they were absent; the initial lift
    # dropped that defaulting, leaving `admit_grant`'s embed re-parse
    # (below) a no-op so the credential was never saved and the
    # `reger.saved` check always raised. Restored here so the grant's
    # `anc`/`iss`/`acdc` embeds are actually verified into the local stores
    # — the only place the ACDC body travels in an IPEX grant. Bound to the
    # same `hby.db`/`rgy.reger` the caller admits into.
    kvy = kvy if kvy is not None else eventing.Kevery(db=hby.db)
    tvy = tvy if tvy is not None else teventing.Tevery(db=hby.db, reger=rgy.reger)
    vry = vry if vry is not None else verifying.Verifier(hby=hby, reger=rgy.reger)

    grant, pathed = exchanging.cloneMessage(hby, grant_said)
    if grant is None:
        raise ValueError(f"exn message said={grant_said} not found")

    route = grant.ked['r']
    if route != "/ipex/grant":
        raise ValueError(f"exn said={grant_said} is not a grant message, route={route}")

    embeds = grant.ked['e']
    acdc = embeds["acdc"]

    # keripy's ipexGrantExn no longer emits a `reg` embed; the receiver
    # reconstructs TEL/registry state from `anc` + `iss`. Parse only the
    # labels the grant actually carries, version-agnostically (see
    # _embed_serder — coring.Sadder is v2-only and breaks the v1-hold).
    for label in ("anc", "iss", "acdc"):
        ked = embeds.get(label)
        if not ked:
            continue
        sadder = _embed_serder(label, ked)
        ims = bytearray(sadder.raw) + pathed.get(label, b'')
        parsing.Parser(
            kvy=kvy,
            tvy=tvy,
            vry=vry,
            version=_message_version(ims),
        ).parseOne(ims=ims)

    credential_said = acdc["d"]
    if not rgy.reger.saved.get(keys=credential_said):
        raise ValueError(
            f"Credential said={credential_said} did not parse from message said={grant_said}")

    exn, atc = protocoling.ipexAdmitExn(hab=hab, message=message, grant=grant,
                                        dt=helping.nowIso8601())
    admit_said = exn.said
    msg = bytearray(exn.raw)
    msg.extend(atc)

    parsing.Parser().parseOne(ims=bytes(msg), exc=exc, version=_message_version(msg))

    sink.on_event("AdmitDoer", "admit_complete",
                 {"success": True, "credential_said": credential_said})

    return admit_said
