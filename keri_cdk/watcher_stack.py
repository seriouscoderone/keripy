from aws_cdk import Stack
from constructs import Construct


class WatcherStack(Stack):
    """Seam for a future KEL-observing / duplicity-checking watcher. Phase B ships the
    construct API only — no handler. A future ecosystem composes a working watcher here.
    See docs/superpowers/specs/2026-06-13-cdk-phase-b-design.md (Watcher seam)."""

    def __init__(self, scope: Construct, cid: str, *, name: str, domain_name: str,
                 hosted_zone_id: str, witnesses=None, **kw):
        super().__init__(scope, cid, **kw)
        self.name = name
        raise NotImplementedError(
            "WatcherStack is a Phase-B seam: the watcher handler is a future build.")
