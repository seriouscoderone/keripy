"""Hermetic LocalRuntime integration test for schema_host_handler.

Proves that a valid `/schema/cmd/publish` command, processed end-to-end through
the LocalRuntime pipeline, issues a publication_receipt ACDC that VALIDATES
against the STRICT real publication_receipt schema (Task 8). Task 7's e2e test
used a PERMISSIVE inline schema; this test closes the gap by using the handler's
own `svc` (with the real RECEIPT_SCHEMA_SAID) and pinning the schema from
`svc.schemas` into the issuer db before running the pipeline.

Key findings validated:
- schema.json `a` block allows `origin: null` and `priorContributor: null` via oneOf.
- `firstSeen`/`priorContributor` are stamped server-side by the pipeline (not the handler).
- `dt`/`d`/`i` are stamped by the ACDC credentialer.create (not the handler).
"""
import sys
import pathlib

import pytest

from keri.core import scheming
from keri.kering import Kinds
from keri.vdr import credentialing

from keri_serviceaid.local_runtime import LocalRuntime
from keri_serviceaid.providers.artifact_store import LocalArtifactStore

# Import the REAL handler svc (includes the real publication_receipt schema).
# This also proves the module loads cleanly from the examples directory.
_handler_path = (pathlib.Path(__file__).parents[2]
                 / "examples/schema_host/schema_host_handler.py")
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("schema_host_handler", _handler_path)
schema_host_handler = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(schema_host_handler)

# conftest.py puts the test dir on sys.path, so bare imports resolve.
from _exn import make_exn
from test_local_runtime import FakeDeliverer, FakeResolver

# A minimal but valid ACDC schema SAD to publish (will be saidified by the handler).
_RAW_PUBLISHED_SCHEMA = {
    "$id": "",
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "TestWidgetSchema",
    "type": "object",
    "properties": {"name": {"type": "string"}},
}


def _saidify(sad):
    return scheming.Schemer(sed=dict(sad), kind=Kinds.json)


def _saved_count(rgy):
    """Count ACDCs issued into the registry (the always-on ledger).

    reger.saved is a CesrSuber — exposes getTopItemIter() (NOT getItemIter)."""
    return len(list(rgy.reger.saved.getTopItemIter()))


def _build_rt(issuer_hby):
    """Build a LocalRuntime around the real schema_host_handler.svc.

    IMPORTANT: register_schema queues into svc.schemas but LocalRuntime does NOT
    auto-pin them (the cloud runtime does). We pin each schema explicitly before
    constructing the runtime so credentialer.create can resolve the schema SAID."""
    svc = schema_host_handler.svc

    hab = issuer_hby.makeHab(name="schema-publisher")
    rgy = credentialing.Regery(hby=issuer_hby, name="schema-publisher", temp=True)

    # Pin every registered schema into the issuer's db.schema store.
    for sad in svc.schemas:
        schemer = scheming.Schemer(sed=dict(sad), kind=Kinds.json)
        issuer_hby.db.schema.pin(keys=(schemer.said,), val=schemer)

    store = LocalArtifactStore()
    svc.artifact_store = store

    fake_deliverer = FakeDeliverer()
    svc.deliverer = fake_deliverer

    svc.resolver = FakeResolver()

    rt = LocalRuntime(svc, hby=issuer_hby, hab=hab, rgy=rgy)
    return hab, rgy, rt, store, fake_deliverer


def test_strict_schema_publish_issues_receipt(issuer_hby, recipient_pre):
    """Full LocalRuntime pipeline with the real publication_receipt schema.

    Sends /schema/cmd/publish with want_receipt=True (and no origin — tests the
    null-origin path in the schema). Asserts the ACDC was issued into the registry
    WITHOUT raising on strict-schema validation. If credentialer.create raises, the
    test fails with the exact error (NEEDS_CONTEXT signal — do not paper over)."""
    hab, rgy, rt, store, fake = _build_rt(issuer_hby)

    # Build a saidified schema to publish (handler validates + re-saidifies).
    published_schemer = _saidify(_RAW_PUBLISHED_SCHEMA)
    sad = dict(published_schemer.sed)  # $id now populated

    exn = make_exn(
        "/schema/cmd/publish",
        recipient_pre,   # sender = the publisher AID
        hab.pre,         # recipient = the service AID
        {"schema": sad, "want_receipt": True},
        # No "origin" key → handler sets origin=None → allowed by schema (oneOf: null)
    )
    rt._captures["/schema/cmd/publish"].handle(exn, attachments=[])
    rt.process_captured()

    # One publication_receipt ACDC issued into the registry (the always-on ledger).
    assert _saved_count(rgy) == 1, (
        "Expected exactly one publication_receipt issued; got %d. "
        "If 0, the pipeline raised silently on strict-schema validation." % _saved_count(rgy)
    )

    # Receipt was delivered (want_receipt=True).
    assert len(fake.delivered) == 1

    # Artifact stored under its SAID.
    assert store.get(published_schemer.said) is not None


def test_strict_schema_publish_without_receipt_issues_but_not_delivered(issuer_hby, recipient_pre):
    """want_receipt=False: ACDC still issued into the ledger but not delivered."""
    hab, rgy, rt, store, fake = _build_rt(issuer_hby)

    published_schemer = _saidify(_RAW_PUBLISHED_SCHEMA)
    sad = dict(published_schemer.sed)

    exn = make_exn(
        "/schema/cmd/publish",
        recipient_pre,
        hab.pre,
        {"schema": sad, "want_receipt": False},
    )
    rt._captures["/schema/cmd/publish"].handle(exn, attachments=[])
    rt.process_captured()

    assert _saved_count(rgy) == 1
    assert len(fake.delivered) == 0
    assert store.get(published_schemer.said) is not None
