# -*- encoding: utf-8 -*-
"""Is this attestation one I should believe?

Design spec §7, as amended by §14.2. FOUR of the five checks already existed
separately — `egf/verify.py::verify_sad`, `EgfDocument.accepted_schema_saids`, keripy's
TEL state, and edge walking. Their value is in being asked TOGETHER, behind one entry
point the concierge CLI and the HOA both import, because two mechanisms implementing
one rule and drifting is the defect this project has already paid for repeatedly.

WHAT THIS IS NOT. It does not answer "may this actor do this" — that is
`providers/credgate.py`, and it gates YOUR OWN surface reveal. This judges an ARRIVING
FACT. §8 of the spec blurs the two; they are different questions and conflating them is
how an arbitrary AID's mandate-shaped attestation gets acted on.

EVERY CHECK FAILS CLOSED, and the verdict names which ran. A check that was not
applicable is ABSENT from `.checks` rather than True: "we did not look" and "we looked
and it was fine" must never be the same value.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from keri_serviceaid.egf.errors import EgfError
from keri_serviceaid.egf.verify import verify_sad

#: The only TEL state a live credential may be in. Anything else — `revoked`, or None
#: for "could not read" — fails.
_CURRENT = "issued"


@dataclass(frozen=True)
class AttestationVerdict:
    """`ok` is the AND of every check that ran. `checks` names them so a caller can
    report WHICH one failed; `failures` carries the human sentence."""

    ok: bool
    checks: dict = field(default_factory=dict)
    failures: tuple = ()


def verify_attestation(*, acdc: dict, egf, tel_state, manifest=None) -> AttestationVerdict:
    """Verify an arriving attestation against the ecosystem's rules.

    `tel_state` is the caller's TEL read — `"issued"`, `"revoked"`, or None when the
    registry could not be read. None fails: an unreachable registry must not be
    indistinguishable from a good one.

    `manifest`, when given, is `{"raw": bytes, "said": str}` — the ingest manifest the
    actuary's rate program commits to. Absent for attestations that carry none, and its
    check is then absent from the verdict rather than passing vacuously.
    """
    checks, failures = {}, []

    schema = (acdc or {}).get("s")
    accepted = tuple(getattr(egf, "accepted_schema_saids", ()) or ())
    checks["schema_accepted"] = bool(schema) and schema in accepted
    if not checks["schema_accepted"]:
        failures.append(
            f"schema {schema!r} is not in the ecosystem's accepted_schema_saids "
            f"({len(accepted)} accepted); the EGF has not admitted this credential type")

    checks["tel_state_current"] = tel_state == _CURRENT
    if not checks["tel_state_current"]:
        failures.append(
            f"TEL state is {tel_state!r}, not {_CURRENT!r}"
            + ("; the registry could not be read, which fails closed"
               if tel_state is None else ""))

    if manifest is not None:
        try:
            verify_sad(manifest["raw"], manifest["said"])
            checks["manifest_rederives"] = True
        except (EgfError, KeyError, ValueError, TypeError, AttributeError) as ex:
            # EgfError covers both failure modes verify_sad raises: EgfIntegrityError
            # (SAID mismatch) and EgfDocumentError (unparseable/non-dict content).
            # KeyError/TypeError/AttributeError cover a malformed manifest dict itself
            # (missing "raw"/"said", or a "raw" that isn't bytes) — this is a
            # verification boundary, so any of these fails closed rather than
            # propagating as an uncaught exception.
            checks["manifest_rederives"] = False
            failures.append(f"the ingest manifest does not re-derive to its SAID: {ex}")

    return AttestationVerdict(ok=all(checks.values()), checks=checks,
                               failures=tuple(failures))
