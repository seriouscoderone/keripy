"""Verifier extension point + OracleVerifier default.

Verification = sender KEY STATE assurance, separate from reachability (resolve.py)
and authz (authz.py). The shared KEL oracle already carries key events AND witness
receipts, so a local read yields witness-corroborated (tier-2) key state for any
in-domain or served-before AID. The sender KEL is parsed into the oracle BEFORE
this runs (the pipeline does hby.psr.parse), so verify only asserts the tier."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class VerificationError(Exception):
    """Raised when the sender's resolved key state does not meet the tier."""


@dataclass
class KeyState:
    pre: str
    tier: str            # "signed" | "receipts" | "watcher"
    sn: int = 0


TIER_ORDER = {"signed": 0, "receipts": 1, "watcher": 2}


def max_tier(a: str, b: str | None) -> str:
    """The stricter of two tiers (b=None -> a). Raises on an unknown tier."""
    if a is None:
        raise ValueError("max_tier requires a non-None tier for 'a'")
    for t in (a, b):
        if t is not None and t not in TIER_ORDER:
            raise ValueError(f"unknown verifier tier {t!r}")
    if b is None:
        return a
    return a if TIER_ORDER[a] >= TIER_ORDER[b] else b


@runtime_checkable
class Verifier(Protocol):
    def verify(self, sender: str, ims: bytes, hby, *, min_tier: str | None = None) -> KeyState:
        """Assert the assurance tier of `sender`'s key state; raise
        VerificationError if unmet. Returns the resolved KeyState."""
        ...


class OracleVerifier:
    """Default verifier. Tiers:
      - "signed"   (tier-1): sender kever present in the oracle (self-certifying).
      - "receipts" (tier-2, default): witnessed AIDs must have witness receipts
        in the oracle; unwitnessed AIDs (no wits) pass at tier-1-equivalent.
      - "watcher"  (tier-3, FUTURE): not implemented — the keri_cdk watcher seam.
    """

    def __init__(self, tier: str = "receipts"):
        if tier not in ("signed", "receipts", "watcher"):
            raise ValueError(f"unknown verifier tier {tier!r}")
        self.tier = tier

    def verify(self, sender: str, ims: bytes, hby, *, min_tier: str | None = None) -> KeyState:
        tier = max_tier(self.tier, min_tier)   # raises ValueError on unknown min_tier
        if tier == "watcher":
            raise NotImplementedError(
                "watcher (tier-3) verification is a named follow-on (keri_cdk "
                "watcher seam); use tier 'signed' or 'receipts'")

        kever = hby.kevers.get(sender)
        if kever is None:
            raise VerificationError(
                f"no key state for {sender} in the oracle (first-contact KEL not "
                "parsed, or sender unknown)")

        sn = getattr(kever, "sn", 0)
        wits = getattr(kever, "wits", []) or []
        # tier 'receipts' MEANING is M-of-N/TOAD agreement (the duplicity check, per
        # docs/canon/keri-trust-and-verification.md). INTERIM: presence-of->=1 receipt;
        # full M-of-N/TOAD lands with the watcher-tier work (tracked follow-up).
        if tier == "receipts" and wits:
            # Witnessed AID: require at least one witness receipt in the oracle.
            if hby.db.wigs.getLast(keys=(sender,)) is None:
                raise VerificationError(
                    f"{sender} is witnessed but has no witness receipts in the "
                    "oracle — tier 'receipts' unmet (strict default)")
        return KeyState(pre=sender, tier=tier, sn=sn)
