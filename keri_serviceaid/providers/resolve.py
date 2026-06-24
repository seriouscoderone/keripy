"""Resolver extension point + OracleResolver default.

Reachability (where to deliver the reply) is separate from identity and key
state. The oracle is made reachability-complete by sharing ends./locs./eans.
(Task 7), so OracleResolver can resolve an in-domain requester's mailbox from one
local hab.endsFor read. InStream/Oobi are first-contact fallback markers (the
runtime/Deliverer use them as hints; v1 keeps them as named seams)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class Endpoint:
    role: str            # "controller" | "agent" | "mailbox" | "witness"
    eid: str             # endpoint provider AID
    url: str             # first reachable URL (https preferred)


class InStream:
    """Fallback marker: end-roles that rode in the request stream (persisted to
    the private ns on parse). A named seam; OracleResolver reads them via endsFor."""


class Oobi:
    """Fallback marker: resolve the requester's OOBI to discover end-roles.
    A named seam; not auto-driven in v1 (the requester is expected in-domain)."""


@runtime_checkable
class Resolver(Protocol):
    def resolve(self, sender: str, hby) -> Endpoint:
        """Return the Endpoint to deliver the reply to; raise LookupError if none."""
        ...


# Role priority: a direct controller/agent endpoint beats a mailbox beats a witness.
_ROLE_PRIORITY = ("controller", "agent", "mailbox", "witness")


class OracleResolver:
    """Default resolver. Reads hab.endsFor(sender) (now oracle-complete) and picks
    the highest-priority role's endpoint, https preferred."""

    def __init__(self, fallback: list | None = None):
        self.fallback = fallback if fallback is not None else [InStream(), Oobi()]

    def resolve(self, sender: str, hby) -> Endpoint:
        # One Service-AID = one hab per process. Guard the assumption: a stray
        # second hab would otherwise make the endpoint pick a silent coin-flip.
        habs = list(hby.habs.values())
        if not habs:
            raise LookupError("service hab not initialised")
        if len(habs) > 1:
            raise LookupError(f"expected exactly one service hab, found {len(habs)}")
        hab = habs[0]
        ends = hab.endsFor(sender)            # role -> eid -> scheme -> url
        for role in _ROLE_PRIORITY:
            if role in ends and ends[role]:
                eid, locs = next(iter(ends[role].items()))
                url = locs.get("https") or locs.get("http") or next(iter(locs.values()), "")
                if url:
                    return Endpoint(role=role, eid=eid, url=url)
        raise LookupError(
            f"no reachable endpoint for {sender} via the oracle "
            "(in-stream/OOBI first-contact resolution is a named fallback seam)")


class BoundResolver:
    """Resolver bound to one explicit hab — for the local (in-wallet) runtime,
    whose Habery holds many AIDs (so OracleResolver's single-hab assumption does
    not hold). Reads the bound hab's endsFor and picks the highest-priority role,
    https preferred."""

    def __init__(self, hab):
        self.hab = hab

    def resolve(self, sender: str, hby) -> Endpoint:
        ends = self.hab.endsFor(sender)            # role -> eid -> scheme -> url
        for role in _ROLE_PRIORITY:
            if role in ends and ends[role]:
                eid, locs = next(iter(ends[role].items()))
                url = locs.get("https") or locs.get("http") or next(iter(locs.values()), "")
                if url:
                    return Endpoint(role=role, eid=eid, url=url)
        raise LookupError(f"no reachable endpoint for {sender} via bound hab")
