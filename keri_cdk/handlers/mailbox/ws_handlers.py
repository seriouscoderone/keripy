"""WebSocket route handlers for the KERI Mailbox Lambda (Phase 3, §5.3).

Three plain Lambda handlers — no LWA layer, no response streaming:

  connect(event, context)     — $connect route: accept the socket (200)
  disconnect(event, context)  — $disconnect route: remove registry row (idempotent)
  default(event, context)     — $default route: dispatch on action="subscribe"

The subscribe handler (default) bootstraps the Habery (cold-start-cached
across warm invocations), peeks the signed qry, and writes the connection
registry row.

Security gate (subscribe) — NATIVE PARITY (§5.3 DECISION REVISION 2026-06-30)
-------------------------------------------------------------------------------
A registry row is written ONLY when ALL of the following hold:
  1. The decoded body is a valid /mbx qry serder (_detect_mbx_query passes).
  2. The querying AID's prefix (q["i"]) is in _hby.kevers (its KEL is known).

This mirrors the EXACT check keripy applies in Kevery.processQuery: "pre in
kevers" is the only gate; keripy has an explicit "ToDo: neither kvy.processQuery
nor tvy.processQuery actually verify" placeholder (parsing.py:1463) and a
"do signature validation … here" comment in eventing.py:~5435.  BE KERI NATIVE
means the WS subscribe handler DOES NOT add a bespoke verifySigs gate that
keripy itself does not apply.  No signature verification, no signer↔owner
binding.  Abuse / compute-throttle mitigation = WAF (fast-follow, §9).
"""

import base64
import json
import logging
import os
import time

import boto3

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conn_table():
    """Return a boto3 DynamoDB Table resource for the registry table."""
    table_name = os.environ["WS_CONN_TABLE"]
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("MAILBOX_REGION", "us-east-1")
    ddb = boto3.resource("dynamodb", region_name=region)
    return ddb.Table(table_name)


# ---------------------------------------------------------------------------
# $connect
# ---------------------------------------------------------------------------

def connect(event, context):
    """$connect: accept every socket, no auth here (§5.3).

    Authentication is deferred to the subscribe message (action="subscribe")
    because there is no KERI token to inspect at connect time.
    """
    return {"statusCode": 200}


# ---------------------------------------------------------------------------
# $disconnect
# ---------------------------------------------------------------------------

def disconnect(event, context):
    """$disconnect: remove the registry row for this connectionId (§5.3).

    Idempotent — deleting an absent row is not an error.  API GW delivers
    $disconnect best-effort (socket may drop without a clean close).
    """
    connection_id = event["requestContext"]["connectionId"]
    try:
        _conn_table().delete_item(Key={"connectionId": connection_id})
    except Exception as exc:  # pragma: no cover — surface unexpected errors
        logger.error("disconnect DeleteItem failed for %s: %s", connection_id, exc)
    return {"statusCode": 200}


# ---------------------------------------------------------------------------
# $default (subscribe)
# ---------------------------------------------------------------------------

def default(event, context):
    """$default: dispatch on action field; currently only "subscribe" (§5.3).

    subscribe flow (native parity — §5.3 DECISION REVISION 2026-06-30):
      1. Parse JSON envelope {"action":"subscribe","qry":"<base64 CESR bytes>"}.
      2. Decode qry bytes; peek with _detect_mbx_query; REJECT non-/mbx qrys.
      3. Ensure Habery (heavy cold-start, warm-cached via mailbox_handler.init()).
      4. Extract recipient AID (q["i"]).  If pre not in _hby.kevers → error, no row.
      5. PutItem registry row {connectionId, pre, topics, connectedAt, expireAt}.

    No psr.parse on subscribe (no Habery state mutation).
    No signature verification (mirrors keripy native behaviour — see module docstring).
    """
    # --- 1. Parse envelope -------------------------------------------------
    body_raw = event.get("body") or ""
    try:
        envelope = json.loads(body_raw)
    except (json.JSONDecodeError, TypeError):
        return {"statusCode": 400, "error": "body must be JSON"}

    action = envelope.get("action")
    if action != "subscribe":
        return {"statusCode": 400, "error": f"unknown action: {action!r}"}

    # --- 2. Decode qry bytes -----------------------------------------------
    qry_b64 = envelope.get("qry", "")
    if not qry_b64:
        return {"statusCode": 400, "error": "missing qry field"}
    try:
        qry_bytes = base64.b64decode(qry_b64)
    except Exception:
        return {"statusCode": 400, "error": "qry must be base64-encoded CESR bytes"}

    # Peek: must be a /mbx qry.
    # The Lambda bundles the handler dir FLAT at /var/task (asset=_HANDLER_DIR), so
    # mailbox_handler is a TOP-LEVEL module there — NOT keri_cdk.handlers.mailbox.
    # Import flat (works in the Lambda AND in tests via the conftest sys.modules
    # alias); fall back to the package path for any dev context lacking that alias.
    try:
        import mailbox_handler
    except ModuleNotFoundError:
        from keri_cdk.handlers.mailbox import mailbox_handler
    mbx_serder = mailbox_handler._detect_mbx_query(qry_bytes)
    if mbx_serder is None:
        return {"statusCode": 400, "error": "qry is not a valid /mbx query serder"}

    # --- 3. Ensure Habery --------------------------------------------------
    mailbox_handler.init()
    hby = mailbox_handler._hby

    # --- 4. Native-parity gate: recipient AID must be in kevers -----------
    # Mirrors keripy Kevery.processQuery: only "pre in kevers" is checked.
    # Signature verification is intentionally absent (see module docstring).
    q = mbx_serder.ked.get("q") or {}
    pre = q.get("i") or q.get("pre")  # "i" is canonical; "pre" accepted for compat
    topics = q.get("topics") or {}

    if not isinstance(pre, str) or not pre:
        return {"statusCode": 400, "error": "qry q must contain i (str)"}

    if pre not in hby.kevers:
        logger.warning("subscribe rejected: AID %s not in kevers (KEL unknown)", pre)
        return {"statusCode": 403, "error": "AID not known to this mailbox"}

    # --- 5. Write registry row ---------------------------------------------
    now = int(time.time())
    expire_at = now + 7800  # backstop past the 2-hr WS max
    try:
        _conn_table().put_item(Item={
            "connectionId": event["requestContext"]["connectionId"],
            "pre": pre,
            "topics": topics,
            "connectedAt": now,
            "expireAt": expire_at,
        })
    except Exception as exc:
        logger.error("subscribe PutItem failed: %s", exc)
        return {"statusCode": 500, "error": "failed to register connection"}

    return {"statusCode": 200}
