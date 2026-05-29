"""KERI Witness Lambda handler."""

import json
import base64
import os
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Module-level singletons (warm across Lambda invocations)
_hby = None
_hab = None
_parser = None


def _clear_keeper(ks):
    """Remove all data from keeper stores so Habery init can start fresh.

    This is needed when a previous init attempt partially succeeded (wrote key
    material to the keeper) but failed before the Baser's signatory record
    was written, leaving the two databases out of sync.
    """
    for store_name in list(ks._stores):
        try:
            ks._clear_store(store_name)
        except Exception:
            pass


def init():
    """Cold-start: set up Habery with DynamoDB backends and create/load witness Hab."""
    global _hby, _hab, _parser

    from keri.db.dynamodbing import DynamoDBer
    from keri.app.lambding import (
        BASER_STORES, KEEPER_STORES,
        setup_baser, setup_keeper,
    )
    from keri.app.habbing import Habery
    from keri.app.configing import Configer

    name = os.environ.get("WITNESS_NAME", "witness")
    alias = os.environ.get("WITNESS_ALIAS", "witness")
    salt = os.environ.get("WITNESS_SALT")
    region = os.environ.get("WITNESS_REGION", "us-east-1")
    endpoint_url = os.environ.get("WITNESS_ENDPOINT_URL")
    baser_table = os.environ.get("WITNESS_BASER_TABLE")
    keeper_table = os.environ.get("WITNESS_KEEPER_TABLE")

    if baser_table is None:
        baser_table = f"{name}-db"
    if keeper_table is None:
        keeper_table = f"{name}-ks"

    kwa = dict(region=region)
    if endpoint_url:
        kwa["endpoint_url"] = endpoint_url
        # When using DynamoDB Local, create a boto3 session with explicit dummy
        # credentials to prevent SAM CLI's injected STS session tokens from
        # causing UnrecognizedClientException.
        import boto3
        kwa["session"] = boto3.Session(
            aws_access_key_id="fake",
            aws_secret_access_key="fake",
            region_name=region,
        )

    # Baser only — mailbox role split out to sam-mailbox at mailbox.keri.host
    # so we no longer attach Mailboxer subdatabases here. (DynamoDB table
    # still has any prior mbx-related keys from before the strip; harmless,
    # they're never read by this handler now.)
    db = DynamoDBer.open(name=name, stores=BASER_STORES, table_name=baser_table, **kwa)
    setup_baser(db)

    ks = DynamoDBer.open(name=f"{name}-ks", stores=KEEPER_STORES, table_name=keeper_table, **kwa)
    setup_keeper(ks)

    # Use provided salt or generate a fresh one
    if not salt:
        from keri.core.signing import Salter
        salt = Salter().qb64

    # Detect inconsistent state from a previously failed init: the keeper has
    # key material (pidx written) but the baser lacks the signatory record.
    # This happens when Manager.incept() succeeds but processEvent or the
    # hbys write fails before completion.  Clear the keeper so we start clean.
    _pidx_raw = ks.gbls.get("pidx")
    _signatory_pre = db.hbys.get("__signatory__")
    if _pidx_raw is not None and _signatory_pre is None:
        logger.warning("Detected partial init state (pidx=%s but no signatory). "
                       "Clearing keeper for clean restart.", _pidx_raw)
        _clear_keeper(ks)
        # Re-attach sub-databases after clearing
        setup_keeper(ks)

    # Configer uses the filesystem; Lambda only allows /tmp, so use temp=True.
    cf = Configer(name=name, temp=True)

    try:
        _hby = Habery(name=name, temp=False, free=True, db=db, ks=ks, cf=cf, salt=salt)
    except (ValueError, Exception) as exc:
        if "Already incepted" in str(exc):
            logger.warning("Habery init hit 'Already incepted' (%s). "
                           "Clearing keeper and retrying.", exc)
            _clear_keeper(ks)
            setup_keeper(ks)
            _hby = Habery(name=name, temp=False, free=True, db=db, ks=ks, cf=cf, salt=salt)
        else:
            raise

    # Get or create witness Hab (non-transferable)
    _hab = _hby.habByName(alias)
    if _hab is None:
        _hab = _hby.makeHab(name=alias, transferable=False, isith='1', icount=1, ncount=0, nsith='0')

    # Ensure our pre is in db.prefixes after a warm reload. Hab.make() adds it
    # on first creation, but the lambding setup_baser does not repopulate from
    # stored state on subsequent cold starts. Without this, the OOBI handler's
    # 406 authorization gate rejects our own self-OOBI.
    _hby.prefixes.add(_hab.pre)

    # Register witness URL and controller-role authorization so OOBI resolvers
    # get signed /loc/scheme and /end/role/add replies. BADA monotonicity via
    # nowIso8601() stamp makes cold-start re-registration safe (db.*.pin
    # overwrites cleanly on newer timestamps).
    from keri.kering import Roles, Schemes
    from keri.help import helping

    witness_url = os.environ.get("WITNESS_URL", "").strip()
    if witness_url:
        scheme = Schemes.https if witness_url.startswith("https://") else Schemes.http
        stamp = helping.nowIso8601()
        url_msgs = bytearray()
        url_msgs.extend(_hab.makeEndRole(eid=_hab.pre, role=Roles.controller, stamp=stamp))
        url_msgs.extend(_hab.makeEndRole(eid=_hab.pre, role=Roles.witness, stamp=stamp))
        url_msgs.extend(_hab.makeLocScheme(url=witness_url, scheme=scheme, stamp=stamp))
        try:
            _hby.psr.parse(ims=url_msgs)
        except Exception as exc:
            logger.warning("Failed to register witness URL %s: %s", witness_url, exc)
    else:
        logger.warning("WITNESS_URL not set; OOBI responses will not include /loc/scheme")

    # No ForwardHandler — /fwd exn deposits land on sam-mailbox now.
    # Incoming /fwd exns on the witness root POST are parsed by the
    # Exchanger which has no /fwd handler, so they no-op and the request
    # returns 204 with no storage.

    # Set up parser with Kevery for processing incoming events
    _parser = _hby.psr

    return _hby, _hab


def handler(event, context):
    """AWS Lambda entry point -- routes by path + method."""
    global _hby, _hab, _parser

    if _hby is None:
        init()

    path = event.get("path", "/")
    method = event.get("httpMethod", "GET")
    path = path.rstrip("/") or "/"

    try:
        if path == "/" and method in ("POST", "PUT"):
            return handle_cesr_ingest(event)
        elif path == "/receipts" and method == "POST":
            return handle_receipt_post(event)
        elif path == "/receipts" and method == "GET":
            return handle_receipt_get(event)
        elif path == "/query" and method == "GET":
            return handle_query_get(event)
        elif path.startswith("/oobi") and method == "GET":
            return handle_oobi_get(event)
        elif path == "/" and method == "GET":
            return handle_status()
        else:
            return response(404, {"error": f"not found: {method} {path}"})
    except Exception as e:
        return response(500, {"error": str(e)})


def handle_status():
    """GET / -- return witness status and identifier."""
    return response(200, {
        "witness": _hab.pre,
        "alias": _hab.name,
        "sn": _hab.kever.sn,
        "kevers": len(_hby.kevers),
    })


def _extract_cesr_stream(event):
    """Build a CESR ims byte stream from a Lambda HTTP event.

    Supports the keripy HTTP wire formats:
      - kli/streamCESRRequests: event Serder in body, attachments in
        the CESR-ATTACHMENT header (see keri/app/httping.py:154).
        Some clients additionally wrap the header attachments in a
        leading AttachmentGroup counter (-C in CESR v2, -V in older
        v1). We strip that wrapper before merging so the parser sees
        only the inner bare attachments — the parser's enclosed-group
        path can yield indefinitely if the wrapper count doesn't match
        the bytes it can see in a one-shot bounded ims (kerihost issue
        #4). Bare attachments parse cleanly.
      - Inline: full CESR stream (event + attachments) in body alone
        (used by our pytest fixtures and ad-hoc curl calls).

    API Gateway header keys are case-sensitive in the event dict, so
    we look up the header case-insensitively.
    """
    body = get_body_bytes(event)
    headers = event.get("headers") or {}
    attachment = ""
    for k, v in headers.items():
        if k.lower() == "cesr-attachment" and v:
            attachment = v
            break
    ims = bytearray(body)
    if attachment:
        ims.extend(_unwrap_attachment_group(attachment.encode("utf-8")))
    return ims


def _unwrap_attachment_group(attachment):
    """Strip a leading AttachmentGroup counter (-C or -V) from CESR-ATTACHMENT
    header bytes; pass through unchanged if no such wrapper is present.

    keripy streamCESRRequests historically puts raw attachment counters
    (e.g. -AAB<siger>) directly in the header. Some clients (and newer
    keripy code paths) wrap those in an AttachmentGroup counter
    declaring "next N quadlets are attachments for the preceding
    message". Quadlet code: 4 chars, with -C being the CESR v2 code
    and -V being the older v1 code (universal-with-override). Both
    encode the inner-byte count as count*4.
    """
    # Need at least 4 bytes for a quadlet counter
    if len(attachment) < 4:
        return attachment
    # Universal AttachmentGroup quadlet counter starts with -C or -V; both
    # are 4-char counters with chars 2-3 encoding the count in base64.
    if attachment[:2] in (b'-C', b'-V'):
        try:
            from keri.core.counting import Counter
            ctr = Counter(qb64b=bytes(attachment[:4]))
        except Exception:
            return attachment
        # Strip the 4-byte counter; the parser will read whatever bare
        # attachment counters (-AAB siger groups, -EAB datetime, etc.)
        # follow in the inner content.
        return attachment[4:]
    return attachment


def _drain_receipt_cues(hby, hab):
    """Drain Kevery cues, generate witness receipts, return concatenated CESR.

    Re-iterates until the cue queue stays empty across a pass — covers
    the case where a receipt itself produces follow-on cues. Validates
    that we are a witness for each pre before signing; logs and skips
    otherwise. All exceptions are logged with traceback.

    Returns:
        bytearray: concatenated CESR receipts (empty if nothing produced).
    """
    receipts = bytearray()
    while True:
        produced = False
        while hby.kvy.cues:
            cue = hby.kvy.cues.popleft()
            if cue.get("kin") != "receipt":
                continue
            serder = cue.get("serder")
            if serder is None:
                continue
            kever = hby.kevers.get(serder.pre)
            if kever is None:
                logger.warning("receipt cue for unknown pre=%s; skipping",
                               serder.pre)
                continue
            if hab.pre not in kever.wits:
                logger.info("receipt cue for pre=%s; %s not in wits; skipping",
                            serder.pre, hab.pre)
                continue
            try:
                rct = hab.receipt(serder=serder)
                receipts.extend(rct)
                produced = True
            except Exception as exc:
                logger.warning("hab.receipt failed for pre=%s sn=%s: %s",
                               serder.pre, serder.sn, exc, exc_info=True)
        if not produced:
            break
        hby.kvy.processEscrows()
    return receipts


def handle_cesr_ingest(event):
    """POST / -- ingest CESR (events, rpys, optionally /fwd exn no-op).

    Two response paths:
      - Inbound produces witness receipts (controller's inception or
        rotation events): 200 + Content-Type: application/cesr with the
        signed receipts in the body, matching the synchronous receipt-back
        flow that kli's --receipt-endpoint expects.
      - Anything else (rpys, /fwd exns that this witness no longer
        handles, etc.): 204 once psr.parse drains.

    Mailbox-role surface (qry r=/mbx + ForwardHandler) is no longer here —
    it moved to sam-mailbox at mailbox.keri.host.
    """
    ims = _extract_cesr_stream(event)
    if not ims:
        return response(400, {"error": "empty body"})

    # framed=True: each HTTP POST is exactly one message + counted attachments
    # (streamCESRRequests contract). Without this, an AttachmentGroup (-V)
    # wrapper that claims more quadlets than are in the stream causes the
    # parser generator to yield forever waiting for bytes that never arrive
    # — the Lambda then hangs until API Gateway times out (30s). Standard
    # keripy clients send the -V wrapper around their attachments; bare
    # attachments (no wrapper) work either way.
    _hby.psr.parse(ims=ims, framed=True)
    _hby.kvy.processEscrows()
    receipts = _drain_receipt_cues(_hby, _hab)
    if receipts:
        # Re-parse so Kevery routes the witness's own receipts into our
        # db.wigs / db.rcts, where handle_receipt_get can find them later.
        # No framed=True here: receipts is a concatenation of multiple
        # receipt events when multiple cues fire on one inbound request.
        _hby.psr.parse(ims=bytearray(receipts))

    # Synchronous receipt-back when the ingest produced witness receipts.
    # Matches keri.app.indirecting.HttpEnd.on_post reference behavior:
    # standard keripy controllers (WitnessReceiptor -> HTTPMessenger ->
    # streamCESRRequests) POST events to URL root and expect the receipt
    # CESR in the response body. Returning 204 here makes the wallet's
    # WitnessReceiptor hang waiting for receipts that never arrive even
    # though the witness has already stored them in db.wigs.
    if receipts:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/cesr"},
            "body": bytes(receipts).decode("utf-8"),
        }

    return response(204, None)


def handle_receipt_post(event):
    """POST /receipts -- ingest event, return signed witness receipt as CESR.

    Synchronous receipt-back flow used by kli incept --receipt-endpoint
    and any agent calling streamCESRRequests with path='/receipts'.
    Body+CESR-ATTACHMENT header format from real KERI clients is
    accepted (and inline-body-only also works for backward compat).
    """
    ims = _extract_cesr_stream(event)
    if not ims:
        return response(400, {"error": "empty body"})
    # framed=True for the same reason as handle_cesr_ingest: one HTTP
    # request = one frame; -V-wrapped attachments otherwise hang the parser.
    _hby.psr.parse(ims=ims, framed=True)
    _hby.kvy.processEscrows()
    receipts = _drain_receipt_cues(_hby, _hab)
    if not receipts:
        return response(204, None)
    # Re-parse so Kevery routes the witness's own receipts into our
    # db.wigs / db.rcts, where handle_receipt_get can find them later.
    _hby.psr.parse(ims=bytearray(receipts))
    # CESR qb64 is pure ASCII. Return as plain text body (no base64, no
    # isBase64Encoded). API Gateway then sends bytes unchanged regardless
    # of the client's Accept header — kli/signify/keria all default to
    # Accept: */* and would otherwise receive base64 text that their
    # CESR parser cannot decode.
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/cesr"},
        "body": bytes(receipts).decode("utf-8"),
    }


def handle_receipt_get(event):
    """GET /receipts?pre=...&sn=... -- return witness receipts for pre at sn.

    Witness receipts (this witness's signatures) live in db.wigs, not
    db.rcts. Kevery routes non-trans receipt couples to db.wigs when the
    receiptor is in the AID's wits list (see core/eventing.py:4196-4202).
    db.rcts holds trans-receipter receipts only.
    """
    params = event.get("queryStringParameters") or {}
    pre = params.get("pre", "")
    if not pre:
        return response(400, {"error": "pre parameter required"})
    sn = int(params.get("sn", "0"))

    dig = _hby.db.kels.getLast(keys=pre, on=sn)
    if dig is None:
        return response(404, {"error": f"no event at pre={pre} sn={sn}"})
    dig = dig.encode("utf-8") if isinstance(dig, str) else dig

    pre_b = pre.encode("utf-8") if isinstance(pre, str) else pre
    wigs = _hby.db.wigs.get(keys=(pre_b, dig))
    if not wigs:
        return response(404, {"error": "no witness receipts found"})

    return response(200, {
        "pre": pre,
        "sn": sn,
        "witness_receipts": len(wigs),
        "witness_aid": _hab.pre,
    })


def handle_query_get(event):
    """GET /query?pre=...&typ=kel -- serve KEL events."""
    params = event.get("queryStringParameters") or {}
    pre = params.get("pre", "")
    typ = params.get("typ", "kel")

    if not pre:
        return response(400, {"error": "pre parameter required"})

    if pre not in _hby.kevers:
        return response(404, {"error": f"unknown identifier: {pre}"})

    kever = _hby.kevers[pre]

    if typ == "kel":
        # Return key state summary
        return response(200, {
            "pre": pre,
            "sn": kever.sn,
            "said": kever.serder.said,
            "transferable": kever.transferable,
            "keys": [v.qb64 for v in kever.verfers],
            "wits": [w.qb64 for w in kever.wits] if hasattr(kever, 'wits') and kever.wits else [],
        })

    return response(400, {"error": f"unknown query type: {typ}"})


def handle_oobi_get(event):
    """GET /oobi, /oobi/{aid}, /oobi/{aid}/{role}, /oobi/{aid}/{role}/{eid}

    Returns a signed CESR reply stream (KEL + /loc/scheme + /end/role/add)
    mirroring src/keri/end/ending.py:558-617 OOBIEnd.on_get behavior.
    Body is returned as plain ASCII text — CESR qb64 is ASCII-safe so no
    base64 wrapping is needed, and bypassing API Gateway's binary-content
    path ensures Accept: */* clients (kli, signify-ts, keria) receive
    raw CESR rather than base64.
    """
    from keri.kering import Roles

    path = event.get("path", "/oobi")
    parts = [p for p in path.split("/") if p and p != "oobi"]

    # Bare /oobi defaults to self-OOBI (matches OOBIEnd.on_get default)
    aid  = parts[0] if parts else _hab.pre
    role = parts[1] if len(parts) > 1 else None
    eid  = parts[2] if len(parts) > 2 else None

    if aid not in _hby.kevers:
        return response(404, {"error": f"unknown aid: {aid}"})

    kever = _hby.kevers[aid]
    if not _hby.db.fullyWitnessed(kever.serder):
        return response(404, {"error": "not fully witnessed"})

    # We respond only for AIDs we control or are a witness for
    owits = set(kever.wits)
    if aid not in _hby.prefixes and not owits.intersection(_hby.prefixes):
        return response(406, {"error": "not acceptable"})

    eids = [eid] if eid else []
    msgs = _hab.replyToOobi(aid=aid, role=role, eids=eids)
    if not msgs and role is None:
        msgs = _hab.replyToOobi(aid=aid, role=Roles.witness, eids=eids)
        msgs.extend(_hab.replay(aid))

    if not msgs:
        return response(404, {"error": "no oobi content available"})

    # CESR qb64 is pure ASCII. Return as plain text body (no base64, no
    # isBase64Encoded). See handle_receipt_post for the rationale: kli
    # and other real KERI clients use Accept: */* and need the body
    # delivered without API Gateway's base64 round-trip.
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/cesr",
            "KERI-AID": aid,
        },
        "body": bytes(msgs).decode("utf-8"),
    }


# ---- Utilities ----

def get_body_bytes(event):
    """Extract body bytes from API Gateway event."""
    body = event.get("body", "")
    if not body:
        return b""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    return bytes(body)


def response(status, body):
    """Build API Gateway response dict."""
    if body is None:
        return {"statusCode": status}
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body) if isinstance(body, dict) else body,
    }
