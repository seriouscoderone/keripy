# -*- encoding: utf-8 -*-
"""IPEX apply framing + sent-apply enumeration (host-agnostic, single-sig).

An IPEX ``apply`` is the KERI-native credential/role request: schema SAID +
attribute labels + recipient — it carries NO ACDC (ACDC spec, IPEX route
table; ``apply`` is an initiating Disclosee->Discloser message). Framing
mirrors ``providers/issue.py::_frame_grant`` including the TRANSITIONAL v1
protocol pin. Grounding: keri:acdc disclosure-ipex.
"""
from keri.kering import Vrsn_1_0
from keri.vc import protocoling

APPLY_ROUTE = "/ipex/apply"


def _frame_apply(hab, recp, message, schema_said, attrs):
    # TRANSITIONAL v1 hold: same pvrsn pin as _frame_grant (issue.py).
    exn, atc = protocoling.ipexApplyExn(hab=hab, recp=recp, message=message,
                                        schema=schema_said, attrs=attrs,
                                        pvrsn=Vrsn_1_0)
    msg = bytearray(exn.raw)
    msg.extend(atc)
    return exn.said, msg


def frame_apply_for(hby, hab, *, schema_said, recipient, message="",
                    attrs=None, sink=None, return_raw=False):
    """Frame an IPEX apply exn requesting a credential of ``schema_said``.

    Returns the apply exn SAID (or ``(said, raw)`` with ``return_raw=True``).
    Framing does NOT persist: the host must parse the raw stream into the
    sender's own Exchanger for the apply to land in ``hby.db.exns``
    (``Exchanger.processEvent`` persists unconditionally on valid signatures;
    the same contract the grant path relies on).
    """
    said, raw = _frame_apply(hab, recipient, message, schema_said,
                             dict(attrs or {}))
    if sink is not None:
        sink.on_event("ApplyFlow", "apply_framed",
                      {"said": said, "schema_said": schema_said,
                       "recipient": recipient})
    if return_raw:
        return said, bytes(raw)
    return said


def list_sent_applies(hby, sender_pre):
    """Enumerate the sender's own persisted /ipex/apply exns, dt-ascending.

    Consumed by locksmith's ``derive_role_states`` (PENDING derivation) and
    the auto-admit watcher. Row keys: said, schema_said, recipient, message, dt.
    """
    rows = []
    for _keys, serder in hby.db.exns.getTopItemIter():
        ked = serder.ked
        if ked.get("r") != APPLY_ROUTE or ked.get("i") != sender_pre:
            continue
        a = ked.get("a") or {}
        rows.append({"said": serder.said,
                     "schema_said": a.get("s", ""),
                     "recipient": a.get("i", ""),
                     "message": a.get("m", ""),
                     "dt": ked.get("dt", "")})
    rows.sort(key=lambda r: r["dt"])
    return rows
