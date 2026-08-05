"""CredentialGate authorizer — present-then-cache enforcement.

Enforces a command's declared CredentialReq by querying the concierge's HELD
credentials (populated when an IPEX presentation was admitted): the intersection
of `reger.schms[schema]` and `reger.subjs[sender]`, confirmed verified via
`reger.saved`, with a per-request TEL revocation re-check. Commands with no
requires_credential fall through to a base Allowlist (so a single authz provider
serves both gated and ungated routes — mirrors how the Issuer reads cmd.issues)."""
from __future__ import annotations

from keri.kering import Ilks

from .authz import Allowlist


def holds_credential(reger, sender: str, req) -> bool:
    """True iff `sender` holds — as subject/issuee — a SAVED, TEL-active credential
    of `req.schema` (from `req.issuer`, when pinned): `reger.schms[schema] ∩
    reger.subjs[sender]`, confirmed via `reger.saved`, `creder.issuer == req.issuer`,
    and TEL-active via `reger.tevers`.

    Extracted from `CredentialGate.authorize` (Amendment C §14.3/Task 8) so a second
    caller — `keri_serviceaid.egf`'s attestation-verification wire, asking "does this
    arriving attestation's issuer hold the expected role credential" against its OWN
    `reger` — asks the exact same question rather than re-implementing it. Two
    mechanisms answering one question and drifting apart is the defect this project
    keeps paying for; `req` need only duck-type `.schema`/`.issuer` (a bare
    `CredentialReq`, no route/command shape required).
    """
    schema_saids = {s.qb64 for s in reger.schms.get(keys=req.schema.encode("utf-8"))}
    if not schema_saids:
        return False

    for saider in reger.subjs.get(keys=sender.encode("utf-8")):
        said = saider.qb64
        if said not in schema_saids:
            continue
        if reger.saved.get(keys=said) is None:
            continue                            # not fully verified
        creder, _prefixer, _number, _diger = reger.cloneCred(said=said)
        if req.issuer and creder.issuer != req.issuer:
            continue
        try:
            tever = reger.tevers[creder.regid]
        except KeyError:
            continue                            # registry TEL not held
        state = tever.vcState(said)
        if state is None or state.et in (Ilks.rev, Ilks.brv):
            continue                            # never issued / revoked
        return True

    return False


class CredentialGate:
    def __init__(self, *, hby, reger, svc, base=None):
        self.hby = hby   # reserved for future issuer key-state checks (Plan 2)
        self.reger = reger
        self.svc = svc
        self.base = base if base is not None else Allowlist([])

    def authorize(self, req) -> tuple[bool, str]:
        cmd = self.svc.lookup(req.route)
        cred_req = getattr(cmd, "requires_credential", None) if cmd is not None else None
        if cred_req is None:
            return self.base.authorize(req)        # ungated -> base policy

        schema_saids = {s.qb64 for s in self.reger.schms.get(
            keys=cred_req.schema.encode("utf-8"))}
        if not schema_saids:
            return False, f"no held credential of schema {cred_req.schema}"

        if holds_credential(self.reger, req.sender, cred_req):
            return True, ""

        return False, (f"sender {req.sender} holds no valid "
                       f"{cred_req.schema} credential")
