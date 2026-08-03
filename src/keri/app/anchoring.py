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
        """Return [(sn, seal), ...] for seals in events with sequence number > sn."""
        found = []
        for serder in self.hab.db.getEvtPreIter(pre=self.pre):
            esn = serder.sn
            if esn <= sn:
                continue
            for seal in serder.seals or []:
                found.append((esn, seal))
            if esn > self.checkpoint:
                self.checkpoint = esn
        return found
