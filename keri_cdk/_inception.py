"""CloudFormation Custom Resource: incept the Service AID + registry on create.

This is the contractual ONE-TIME creator of the AID and its credential
registry (see issuing.ensure_registry's docstring): creating both at deploy
time means the runtime request path only ever hits the read branch, avoiding
the racing-cold-starts split-brain hazard. Idempotent — runtime.init()
load-or-incepts, so CloudFormation retries are safe. Delete is a no-op (the
AID/keys persist; tearing down identity state is an operator decision, never
an implicit side effect of stack deletion).

The CR is ALSO the contractual creator of the keeper secret
(``keri/<alias>/keeper``): before incepting, it get-or-creates that secret with
a freshly-minted salt + bran (race-safe, create-only — never overwriting an
existing secret). The runtime then opens the keeper over that secret, so the
keystore is encrypted under the bran from the very first cold start. (Without
this the runtime would lazily create the secret with no salt/bran, leaving the
keeper UNENCRYPTED.)"""
from __future__ import annotations

import json
import logging

from keri.core.signing import Salter
from keri.db.secretkeeper import SecretStore

# Dual-mode: in tests/CDK synth this module is imported as ``keri_cdk._inception``
# and the serviceaid runtime is a package (keri_cdk.handlers.serviceaid.*). On
# Lambda the CR path ships this file flat in /var/task alongside the serviceaid
# runtime modules (config.py, runtime.py, ...), so the package path is absent
# and we fall back to the bare module names. See handler.py for the rationale.
try:
    from keri_cdk.handlers.serviceaid import runtime
    from keri_cdk.handlers.serviceaid.config import Config
except ImportError:  # pragma: no cover - flat /var/task on Lambda
    import runtime
    from config import Config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _mint_keeper() -> str:
    """Mint a fresh keeper secret body. ``Salter().qb64`` is 24 chars; the
    bran slice ``[2:23]`` strips the 2-char count code and yields exactly 21
    chars — keripy's 21-char aeid (passcode) minimum. ``keeper`` is None until
    the first incept flushes the keystore blob."""
    return json.dumps({
        "v": 1,
        "salt": Salter().qb64,
        "bran": Salter().qb64[2:23],
        "keeper": None,
    })


def on_event(event, context):
    request_type = event["RequestType"]
    if request_type in ("Create", "Update"):
        # Note: if SERVICEAID_ALIAS changes, init() returns a new pre → CFN
        # treats this as a resource replacement and sends Delete for the old
        # pre, which no-ops here. The old AID/keys are orphaned (intentional;
        # recovery requires a manual key ceremony).
        cfg = Config.from_env()
        # Build the SecretStore from the DECOUPLED secret endpoint (NOT
        # cfg.endpoint_url, which targets DynamoDB / DynamoDB-Local).
        store = SecretStore(region=cfg.region,
                            endpoint_url=cfg.secret_endpoint_url)
        created, _ = store.get_or_create(cfg.keeper_secret, _mint_keeper)
        logger.info("Keeper secret %s %s", cfg.keeper_secret,
                    "created" if created else "already present")

        runtime.reset()
        state = runtime.init()      # reads config from env; incepts if absent
        pre = state.hab.pre
        logger.info("Service AID inception complete: alias=%s pre=%s",
                    state.cfg.alias, pre)
        return {"PhysicalResourceId": pre, "Data": {"ServiceAidPre": pre}}

    # Delete: keep the AID and its keys; nothing to undo.
    return {"PhysicalResourceId": event.get("PhysicalResourceId", "noop")}
