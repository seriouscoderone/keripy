"""Exchanger behavior that stashes verified exns for synchronous dispatch.

Shared by both runtimes (Lambda and local) so neither has to re-implement it and
the local runtime need not import the DynamoDB-laden runtime.py."""
from __future__ import annotations


class _CaptureHandler:
    """Exchanger behavior that stashes verified exns for synchronous dispatch."""

    def __init__(self, resource):
        self.resource = resource
        self.captured = []   # list of (serder, attachments)

    def verify(self, serder, attachments=None, **kw):
        return True

    def handle(self, serder, attachments=None, **kw):
        self.captured.append((serder, attachments or []))

    def drain(self):
        """Return all captured exns and clear the buffer (sole read path —
        prevents a stale capture from a prior request leaking into a later
        response on a warm runtime)."""
        out, self.captured = self.captured, []
        return out
