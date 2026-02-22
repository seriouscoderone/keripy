# -*- encoding: utf-8 -*-
"""
keri.app.processing module

HIO-free call/return KERI message processing facility.

Provides Processer class that composes the same business logic objects
(Kevery, Parser, Hab, Exchanger) used by HIO-based Reactor/Directant
into a structured return interface suitable for serverless, request/response,
and synchronous testing deployments.

"""
from collections import namedtuple

from .. import help

logger = help.ogler.getLogger()


Processage = namedtuple("Processage", "outbound queries notifications")
Processage.__doc__ = """Structured result from Processer.process / processEscrows.

Fields:
    outbound (list[bytearray]): messages to send (receipts, replies, replays)
    queries (list[dict]): pending queries/resolutions needed
    notifications (list[dict]): app-level signals (keyStateSaved, saved, etc.)
"""


def _mergeProcessages(*pages):
    """Merge multiple Processage results into one.

    Parameters:
        *pages (Processage): results to merge

    Returns:
        Processage: combined result
    """
    outbound = []
    queries = []
    notifications = []
    for p in pages:
        outbound.extend(p.outbound)
        queries.extend(p.queries)
        notifications.extend(p.notifications)
    return Processage(outbound=outbound, queries=queries,
                      notifications=notifications)


class Processer:
    """Call/return KERI message processing facility. HIO-free alternative
    to Reactor/Directant.

    Composes the same business logic objects (Kevery, Parser, Hab, Exchanger)
    into a structured return interface. Each call processes what CAN be done
    now and returns results telling the caller what async work remains.

    KERI is fundamentally asynchronous — full event lifecycle spans multiple
    invocations. Processer handles the per-invocation synchronous work:
      - process(ims): ingestion — validate, verify, escrow-or-apply, receipt
      - processEscrows(): resolution — check escrowed events, retry processing

    Attributes:
        hby (Habery): shared database environment
        hab (Hab|None): local hab for signing receipts/replies. If None,
            attempts to find appropriate hab from hby.habs.
        local (bool): True means event source is local (protected)
        kevery (Kevery): key event message processor
        parser (Parser): stream parser
        revery (Revery): reply message processor
        tvy (Tevery|None): TEL event processor
        vry (Verifier|None): credential verifier
        exc (Exchanger): exchange message processor
    """

    def __init__(self, hby, *, hab=None, tvy=None, vry=None, exc=None,
                 local=True):
        """Initialize instance.

        Parameters:
            hby (Habery): shared database environment with .kvy, .psr, .rvy, .exc
            hab (Hab|None): local hab for signing receipts/replies.
                None means look up from hby.habs per event.
            tvy (Tevery|None): TEL event processor
            vry (Verifier|None): credential verifier
            exc (Exchanger|None): exchange processor. None means use hby.exc.
            local (bool): True means treat events as local (protected)
        """
        self.hby = hby
        self.hab = hab
        self.local = local
        self.kevery = hby.kvy
        self.parser = hby.psr
        self.revery = hby.rvy
        self.tvy = tvy
        self.vry = vry
        self.exc = exc if exc is not None else hby.exc

    def process(self, ims):
        """Ingestion phase: parse and process all messages in buffer.

        Deserializes, validates, verifies signatures (if key state known),
        escrows or applies events, and generates receipt/reply cues.

        Parameters:
            ims (bytearray): incoming message stream

        Returns:
            Processage: structured result with outbound messages, queries,
                and notifications
        """
        self.parser.parse(ims=ims,
                          kvy=self.kevery,
                          tvy=self.tvy,
                          exc=self.exc,
                          rvy=self.revery,
                          vry=self.vry,
                          local=self.local)
        return self._drainCues()

    def processEscrows(self):
        """Resolution phase: process escrowed events that may now be finalized.

        Checks escrowed events whose dependencies may have been resolved
        since last check (e.g., out-of-order events whose prior events
        have arrived, partially signed events that now have enough sigs).

        Returns:
            Processage: structured result with any newly resolved messages
        """
        self.kevery.processEscrows()
        if self.tvy:
            self.tvy.processEscrows()
        if self.exc:
            self.exc.processEscrow()
        return self._drainCues()

    def processOnce(self, ims):
        """Convenience: ingestion + resolution in one call.

        Parameters:
            ims (bytearray): incoming message stream

        Returns:
            Processage: merged result of process() and processEscrows()
        """
        r1 = self.process(ims)
        r2 = self.processEscrows()
        return _mergeProcessages(r1, r2)

    def _drainCues(self):
        """Drain all processor cue queues and categorize into buckets.

        Returns:
            Processage: categorized cues
        """
        outbound, queries, notifications = [], [], []
        for cues in self._allCues():
            while cues:
                cue = cues.pull()
                self._categorizeCue(cue, outbound, queries, notifications)
        return Processage(outbound=outbound, queries=queries,
                          notifications=notifications)

    def _allCues(self):
        """Yield all active cue deques.

        Yields:
            Deck: cue deque from each active processor
        """
        yield self.kevery.cues
        if self.revery and self.revery.cues is not self.kevery.cues:
            yield self.revery.cues
        if self.tvy:
            yield self.tvy.cues
        if self.exc:
            yield self.exc.cues

    def _categorizeCue(self, cue, outbound, queries, notifications):
        """Route a single cue dict into the appropriate bucket.

        Parameters:
            cue (dict): cue dict with 'kin' key indicating type
            outbound (list): accumulator for outbound messages
            queries (list): accumulator for pending queries
            notifications (list): accumulator for app-level signals
        """
        kin = cue["kin"]

        if kin in ("receipt",):
            hab = self._resolveHab(cue)
            if hab is not None:
                serder = cue["serder"]
                try:
                    msg = hab.receipt(serder)
                    outbound.append(msg)
                except Exception as ex:
                    logger.debug("Processer: receipt generation failed: %s", ex)
                    notifications.append(cue)
            else:
                notifications.append(cue)

        elif kin in ("witness",):
            hab = self._resolveHab(cue)
            if hab is not None:
                serder = cue["serder"]
                try:
                    msg = hab.witness(serder)
                    outbound.append(msg)
                except Exception as ex:
                    logger.debug("Processer: witness receipt failed: %s", ex)
                    notifications.append(cue)
            else:
                notifications.append(cue)

        elif kin in ("replay",):
            msgs = cue.get("msgs")
            if msgs:
                outbound.append(msgs)
            else:
                notifications.append(cue)

        elif kin in ("reply",):
            hab = self._resolveHab(cue)
            if hab is not None:
                data = cue.get("data")
                route = cue.get("route")
                try:
                    msg = hab.reply(data=data, route=route)
                    outbound.append(msg)
                except Exception as ex:
                    logger.debug("Processer: reply generation failed: %s", ex)
                    notifications.append(cue)
            else:
                notifications.append(cue)

        elif kin in ("query", "telquery"):
            queries.append(cue)

        else:
            notifications.append(cue)

    def _resolveHab(self, cue):
        """Find the appropriate local Hab for signing a response to a cue.

        Parameters:
            cue (dict): cue dict, may contain 'serder' with event prefix

        Returns:
            Hab|None: local hab to use for signing, or None if not found
        """
        if self.hab is not None:
            return self.hab

        # Try to find any local hab from hby
        habs = self.hby.habs
        if habs:
            # Return the first available local hab
            return next(iter(habs.values()), None)

        return None
