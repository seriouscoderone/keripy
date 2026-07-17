"""ProgressSink: the observer seam issue/deliver providers emit through.

A Qt host (Locksmith) and a headless host (CLI) can both consume the SAME
progress events by handing a `ProgressSink` implementation to a provider.
Event names deliberately preserve Locksmith's existing vocabulary
(`("IssueCredentialDoer", "credential_issued", {...})`,
`("SendGrantDoer", "send_complete", {...})`) so its existing signal
filters/`_on_doer_event` gates keep working unmodified against the new
serverless providers.

Providers default to `NullSink` (`self._sink = sink or NullSink()`), so
passing no sink is a true no-op: zero behavior change beyond the sink calls
themselves.
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable


@runtime_checkable
class ProgressSink(Protocol):
    def on_event(self, source: str, event_type: str, data: dict) -> None:
        """Observe one progress event.

        `source` names the emitting doer/component (Locksmith vocabulary,
        e.g. "IssueCredentialDoer"), `event_type` names the event
        (e.g. "credential_issued"), `data` carries event-specific payload.
        """
        ...


class NullSink:
    """Default sink: swallows every event. Keeps providers behavior-neutral
    when no observer is wired up."""

    def on_event(self, source: str, event_type: str, data: dict) -> None:
        pass


class LogSink:
    """Sink that logs each event via stdlib `logging` at INFO level. Useful
    for headless hosts (CLI) that want visibility without a UI to route to."""

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger(__name__)

    def on_event(self, source: str, event_type: str, data: dict) -> None:
        self._logger.info("%s: %s %s", source, event_type, data)
