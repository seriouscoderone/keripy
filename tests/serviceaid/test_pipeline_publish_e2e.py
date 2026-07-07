"""Hermetic e2e for the pipeline `publish` branch, driving the full LocalRuntime
path (mirrors tests/serviceaid/test_local_runtime.py).

A `/schema/cmd/publish` command returns `Reply.publish(...)`; the pipeline stores
the artifact in the injected ArtifactStore, merges first-seen provenance into the
receipt attributes, issues a `publication_receipt` ACDC into the registry (the
always-on ledger), and delivers the grant ONLY when `want_receipt` is set.

Assertions use the reger's `getTopItemIter()` (CesrSuber/SerderSuber expose this,
NOT getItemIter) to count issued receipts — essential for the want_receipt=False
case, which is issued-but-not-delivered.
"""
from keri.core import scheming
from keri.kering import Kinds
from keri.vdr import credentialing

from keri_serviceaid import ServiceAid, Reply
from keri_serviceaid.local_runtime import LocalRuntime
from keri_serviceaid.providers.artifact_store import LocalArtifactStore

# conftest.py puts the test dir on sys.path, so these bare imports resolve.
from _exn import make_exn
from test_local_runtime import FakeDeliverer, FakeResolver

RECEIPT_SCHEMA = {
    "$id": "", "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "PublicationReceipt", "type": "object",
    "properties": {"v": {"type": "string"}, "d": {"type": "string"},
                   "i": {"type": "string"}, "ri": {"type": "string"},
                   "s": {"type": "string"},
                   "a": {"type": "object"}},
    "additionalProperties": False, "required": ["v", "d", "i", "ri", "s", "a"]}

PUBLISHED_SCHEMA = {"$id": "", "$schema": "http://json-schema.org/draft-07/schema#",
                    "title": "Widget", "type": "object", "properties": {}}


def _published_said():
    return scheming.Schemer(sed=dict(PUBLISHED_SCHEMA), kind=Kinds.json).said


def _saved_count(rgy):
    """Count publication_receipts issued into the registry (the always-on ledger).

    reger.saved is a CesrSuber; it exposes getTopItemIter() (NOT getItemIter)."""
    return len(list(rgy.reger.saved.getTopItemIter()))


def _build(issuer_hby):
    hab = issuer_hby.makeHab(name="schema-publisher")
    rgy = credentialing.Regery(hby=issuer_hby, name="schema-publisher", temp=True)
    svc = ServiceAid(alias="schema-publisher")
    receipt_said = svc.register_schema(dict(RECEIPT_SCHEMA))
    # register_schema queues the SAD; the pipeline issuer needs the schemer pinned
    # in the db to create the ACDC.
    for sad in svc.schemas:
        schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
        issuer_hby.db.schema.pin(keys=(schemer.said,), val=schemer)

    @svc.command(route="/schema/cmd/publish", issues=receipt_said)
    def publish(req):
        schemer = scheming.Schemer(sed=dict(req.payload["schema"]), kind=Kinds.json)
        return Reply.publish(recipient=req.sender, artifact_said=schemer.said,
                             artifact_bytes=schemer.raw,
                             attributes={"schemaSaid": schemer.said,
                                         "schemaKind": "ACDC-schema",
                                         "publisher": req.sender},
                             want_receipt=req.payload.get("want_receipt", False))

    store = LocalArtifactStore()
    svc.artifact_store = store
    fake = FakeDeliverer()
    svc.deliverer = fake
    svc.resolver = FakeResolver()
    rt = LocalRuntime(svc, hby=issuer_hby, hab=hab, rgy=rgy)
    return hab, rgy, rt, store, fake


def test_publish_stores_artifact_and_issues_receipt_first_seen(issuer_hby, recipient_pre):
    hab, rgy, rt, store, fake = _build(issuer_hby)
    exn = make_exn("/schema/cmd/publish", recipient_pre, hab.pre,
                   {"schema": PUBLISHED_SCHEMA, "want_receipt": True})
    rt._captures["/schema/cmd/publish"].handle(exn, attachments=[])
    rt.process_captured()

    # artifact stored under its SAID
    assert store.get(_published_said()) is not None
    # exactly one publication_receipt issued into the registry (the ledger)
    assert _saved_count(rgy) == 1
    # and delivered (want_receipt=True)
    assert len(fake.delivered) == 1


def test_publish_without_receipt_issues_but_does_not_deliver(issuer_hby, recipient_pre):
    hab, rgy, rt, store, fake = _build(issuer_hby)
    exn = make_exn("/schema/cmd/publish", recipient_pre, hab.pre,
                   {"schema": PUBLISHED_SCHEMA, "want_receipt": False})
    rt._captures["/schema/cmd/publish"].handle(exn, attachments=[])
    rt.process_captured()

    # artifact stored, receipt issued into the ledger, but NOT delivered
    assert store.get(_published_said()) is not None
    assert _saved_count(rgy) == 1          # ledger entry issued (always-on)
    assert len(fake.delivered) == 0        # delivery not requested


def test_second_publish_by_other_sender_is_not_first_seen(issuer_hby, recipient_pre):
    """A second publish of the SAME SAD by a different sender must record
    first_seen=False + a priorContributor pointing at the original publisher."""
    hab, rgy, rt, store, fake = _build(issuer_hby)

    # First publish (by recipient_pre) — first-seen.
    exn1 = make_exn("/schema/cmd/publish", recipient_pre, hab.pre,
                    {"schema": PUBLISHED_SCHEMA, "want_receipt": True})
    rt._captures["/schema/cmd/publish"].handle(exn1, attachments=[])
    rt.process_captured()

    # Second publish of the same SAD by a DIFFERENT sender (hab.pre) — not first.
    exn2 = make_exn("/schema/cmd/publish", hab.pre, hab.pre,
                    {"schema": PUBLISHED_SCHEMA, "want_receipt": True})
    rt._captures["/schema/cmd/publish"].handle(exn2, attachments=[])
    rt.process_captured()

    # Two receipts issued (one per publish); both delivered.
    assert _saved_count(rgy) == 2
    assert len(fake.delivered) == 2

    # Inspect the two issued receipts' attributes: exactly one first_seen=True
    # and one first_seen=False whose priorContributor names the first publisher.
    first_seen_flags = []
    prior_contributors = []
    for keys, saider in rgy.reger.saved.getTopItemIter():
        creder = rgy.reger.creds.get(keys=(saider.qb64,))
        attrib = creder.sad["a"]
        first_seen_flags.append(attrib.get("firstSeen"))
        prior_contributors.append(attrib.get("priorContributor"))

    assert sorted(first_seen_flags, key=lambda x: x is True) == [False, True]
    # The not-first receipt names recipient_pre as the prior contributor.
    non_first = [pc for fs, pc in zip(first_seen_flags, prior_contributors) if fs is False]
    assert len(non_first) == 1
    assert non_first[0] == {"aid": recipient_pre}
