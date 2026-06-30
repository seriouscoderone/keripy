"""WebSocket route handlers for the KERI Mailbox Lambda (Phase 3, §5.3).

Three plain Lambda handlers — no LWA layer, no response streaming:

  connect(event, context)     — $connect route: accept the socket (200)
  disconnect(event, context)  — $disconnect route: remove registry row (idempotent)
  default(event, context)     — $default route: dispatch on action="subscribe"

The subscribe handler (default) is the heavy one: it bootstraps the Habery
(cold-start-cached across warm invocations), parses + cryptographically
verifies the signed qry, and writes the connection registry row.

Security gate (subscribe)
--------------------------
A registry row is written ONLY when ALL of the following hold:
  1. The decoded body is a valid /mbx qry serder (_detect_mbx_query passes).
  2. The qry carries a signed TransLastIdxSig group in the attachment.
  3. The querying AID's prefix is in _hby.kevers (its KEL is known).
  4. At least one attached indexed signature verifies against the kever's
     current verfers (eventing.verifySigs returns >=1 valid siger).

Why explicit verifySigs?  kvy.processQuery (called by psr.parse) explicitly
does NOT verify signatures (see parsing.py:1463 "ToDo: neither ... actually
verify").  It only checks `pre in kevers`.  If we relied solely on parse()
we would accept a structurally valid qry with a forged/wrong signature as
long as we happened to know the AID.  The explicit verifySigs call closes
that hole: a forgery from an attacker who guesses a known AID is rejected
unless they hold the private key.
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

    subscribe flow:
      1. Parse JSON envelope {"action":"subscribe","qry":"<base64 CESR bytes>"}.
      2. Decode qry bytes; peek with _detect_mbx_query; REJECT non-/mbx qrys.
      3. Ensure Habery (heavy cold-start, warm-cached via mailbox_handler.init()).
      4. Parse the signed qry through _hby.psr to let kvy process it.
      5. SECURITY GATE: verify >=1 attached sig against the AID's current kever
         verfers.  Unknown AID or bad sig => 403, no row written.
      6. PutItem registry row {connectionId, pre, topics, connectedAt, expireAt}.
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

    # Peek: must be a /mbx qry
    from keri_cdk.handlers.mailbox import mailbox_handler
    mbx_serder = mailbox_handler._detect_mbx_query(qry_bytes)
    if mbx_serder is None:
        return {"statusCode": 400, "error": "qry is not a valid /mbx query serder"}

    # --- 3. Ensure Habery --------------------------------------------------
    mailbox_handler.init()
    hby = mailbox_handler._hby

    # --- 4. Parse the signed qry through the Habery's parser ---------------
    # psr.parse calls kvy.processQuery which checks pre in kevers.  Any parse
    # failure (e.g. malformed attachment) surfaces as an exception here.
    try:
        hby.psr.parse(ims=bytearray(qry_bytes), framed=True)
    except Exception as exc:
        logger.warning("psr.parse failed for subscribe qry: %s", exc)
        return {"statusCode": 400, "error": "qry parse failed"}

    # --- 5. SECURITY GATE --------------------------------------------------
    q = mbx_serder.ked.get("q") or {}
    # keripy eventing.py:5532: mbx qry uses q["i"] for recipient AID, q["src"] for sender
    pre = q.get("i") or q.get("pre")  # "i" is canonical; "pre" accepted for compat
    topics = q.get("topics") or {}

    if not isinstance(pre, str) or not pre or not isinstance(topics, dict):
        return {"statusCode": 400, "error": "qry q must contain i (str) and topics (dict)"}

    if pre not in hby.kevers:
        logger.warning("subscribe rejected: AID %s not in kevers (KEL unknown)", pre)
        return {"statusCode": 403, "error": "AID not known to this mailbox"}

    # Cryptographic signature verification — closes the gap in processQuery.
    # We use the sender's (src) kever since they sign the qry.
    src = q.get("src") or pre
    verified = _verify_qry_sig(qry_bytes, mbx_serder, hby, signer_pre=src)
    if not verified:
        logger.warning("subscribe rejected: signature verification failed for src %s", src)
        return {"statusCode": 403, "error": "qry signature verification failed"}

    # --- 6. Write registry row ---------------------------------------------
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


# ---------------------------------------------------------------------------
# Signature verification helper
# ---------------------------------------------------------------------------

def _verify_qry_sig(qry_bytes, mbx_serder, hby, signer_pre=None):
    """Cryptographically verify the TransLastIdxSig on qry_bytes.

    Returns True if at least one attached indexed signature verifies against
    the signer AID's current kever verfers.  Returns False otherwise.

    This closes the gap noted in parsing.py:1463: processQuery does not
    actually verify signatures — it only checks kevers membership.

    Attachment structure produced by hab.endorse(serder, last=True):
      <Frame -V> <TransLastIdxSigGroups -H, count=N>
        [<Prefixer> <CtrlIdxSigs -A, count=K> <Siger>xK]xN

    We walk the attachment bytes using Counter / Prefixer / Siger primitives
    then call eventing.verifySigs against the kever's current verfers.
    """
    from keri.core import eventing
    from keri.core.counting import Counter
    from keri.core.coring import Prefixer
    from keri.core.indexing import Siger

    try:
        serder_len = len(mbx_serder.raw)
        attachment = bytearray(qry_bytes[serder_len:])
        if not attachment:
            return False

        # Skip outer Frame counter (-V / --V)
        outer = Counter(qb64b=attachment)
        outer_sz = outer.sizes[outer.code]
        del attachment[:outer_sz.hs + outer_sz.ss]

        # TransLastIdxSigGroups counter (-H V1 / -Y V2); count = number of groups
        lsgs_ctr = Counter(qb64b=attachment)
        lsgs_sz = lsgs_ctr.sizes[lsgs_ctr.code]
        num_groups = lsgs_ctr.count
        del attachment[:lsgs_sz.hs + lsgs_sz.ss]

        source_pre = None
        all_sigers = []

        for _ in range(num_groups):
            # Prefixer = source AID for this group
            pfxr = Prefixer(qb64b=attachment)
            del attachment[:len(pfxr.qb64b)]

            # ControllerIdxSigs counter (-A / --A); count = sigs in this group
            ctrl_ctr = Counter(qb64b=attachment)
            ctrl_sz = ctrl_ctr.sizes[ctrl_ctr.code]
            num_sigs = ctrl_ctr.count
            del attachment[:ctrl_sz.hs + ctrl_sz.ss]

            group_sigers = []
            for _ in range(num_sigs):
                siger = Siger(qb64b=attachment)
                group_sigers.append(siger)
                del attachment[:len(siger.qb64b)]

            # Keep the last group (mirrors parsing.py:1466 "use last one if more")
            source_pre = pfxr.qb64
            all_sigers = group_sigers

    except Exception as exc:
        logger.debug("attachment extraction failed: %s", exc)
        return False

    if not source_pre or not all_sigers:
        return False

    # The TransLastIdxSig source must match the expected signer
    expected_src = signer_pre or source_pre
    if source_pre != expected_src:
        logger.warning("lsgs source %s != expected signer %s", source_pre, expected_src)
        return False

    if source_pre not in hby.kevers:
        return False

    kever = hby.kevers[source_pre]
    vsigers, vindices = eventing.verifySigs(
        raw=mbx_serder.raw,
        sigers=all_sigers,
        verfers=kever.verfers,
    )
    return len(vsigers) > 0
