"""CloudFormation Custom Resource: incept the Service AID + registry on create.

This is the contractual ONE-TIME creator of the AID and its credential
registry (see issuing.ensure_registry's docstring): creating both at deploy
time means the runtime request path only ever hits the read branch, avoiding
the racing-cold-starts split-brain hazard. Idempotent — runtime.init()
load-or-incepts, so CloudFormation retries are safe. Delete is a no-op (the
AID/keys persist; tearing down identity state is an operator decision, never
an implicit side effect of stack deletion)."""
from __future__ import annotations

import logging

from serviceaid import runtime

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def on_event(event, context):
    request_type = event["RequestType"]
    if request_type in ("Create", "Update"):
        # Note: if SERVICEAID_ALIAS changes, init() returns a new pre → CFN
        # treats this as a resource replacement and sends Delete for the old
        # pre, which no-ops here. The old AID/keys are orphaned (intentional;
        # recovery requires a manual key ceremony).
        runtime.reset()
        state = runtime.init()      # reads config from env; incepts if absent
        pre = state.hab.pre
        logger.info("Service AID inception complete: alias=%s pre=%s",
                    state.cfg.alias, pre)
        return {"PhysicalResourceId": pre, "Data": {"ServiceAidPre": pre}}

    # Delete: keep the AID and its keys; nothing to undo.
    return {"PhysicalResourceId": event.get("PhysicalResourceId", "noop")}
