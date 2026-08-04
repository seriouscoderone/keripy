# -*- encoding: utf-8 -*-
"""Watch a peer's KEL for anchors you have not seen.

keripy's queriers (AnchorQuerier, LogQuerier, SeqNoQuerier) all wait for a
*known* thing to appear. A subscriber needs the other direction: what is new
since I last looked. This module is that, and nothing more — it does not
retrieve sealed bodies (see app.prodding) and does not verify them (see
core.sealing).
"""


class AnchorWatcher:
    """Reports seals anchored in a peer's KEL newer than a checkpoint."""

    def __init__(self, hab, pre):
        self.hab = hab
        self.pre = pre
        self.checkpoint = -1

    def since(self, sn):
        """Return [(sn, seal), ...] for digest seals in events with sn > `sn`.

        Only the ACCEPTED event at each sequence number is read.
        `getEvtLastPreIter` yields the last duplicate at each sn, which is the
        accepted one; its sibling `getEvtPreIter` replays superseded duplicates
        too. That difference is the whole point: superseding recovery is how a
        controller repudiates events an attacker signed with a stolen key, and
        an anchor in a repudiated event must never be reported. The `sn + 1`
        start is inclusive, which is what makes the contract strictly `> sn`,
        and it means a poll reads only the tail of the KEL rather than all of it.

        Only seals carrying a `d` are returned. SealLast/SealBack (`{"i": ...}`),
        SealRoot (`{"rd": ...}`) and whatever else a controller put in an `a`
        block — `hab.interact` accepts non-mappings — are legal KEL content and
        are skipped. This module finds *digest* anchors; other seal kinds are
        not its business, and every caller reads `seal["d"]`.

        Advances .checkpoint to the highest sequence number EXAMINED — which is
        not necessarily the highest one returned, because events without digest
        seals are examined and contribute nothing. checkpoint is a scan cursor,
        so passing it back as `sn` never re-examines an event.
        """
        found = []
        for serder in self.hab.db.getEvtLastPreIter(pre=self.pre, sn=sn + 1):
            esn = serder.sn
            for seal in serder.seals or []:
                if isinstance(seal, dict) and "d" in seal:
                    found.append((esn, seal))
            self.checkpoint = max(self.checkpoint, esn)
        return found
