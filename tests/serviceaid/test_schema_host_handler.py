"""Unit tests for schema_host_handler: validate_public_schema guardrail +
publish_schema command return shape (via TestRuntime, no keripy side effects)."""
import pytest

from keri_serviceaid import TestRuntime

import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location(
    "schema_host_handler",
    pathlib.Path(__file__).parents[2] / "examples/schema_host/schema_host_handler.py")
schema_host_handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(schema_host_handler)

VALID_SCHEMA = {"$id": "", "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "Widget", "type": "object", "properties": {}}


def _saidify(sad):
    from keri.core import scheming
    from keri.kering import Kinds
    return scheming.Schemer(sed=dict(sad), kind=Kinds.json)


def test_publish_returns_publish_reply_for_valid_schema():
    schemer = _saidify(VALID_SCHEMA)
    sad = dict(schemer.sed)   # $id now populated
    reply = TestRuntime(schema_host_handler.svc).send(
        route="/schema/cmd/publish", sender="EAlice",
        payload={"schema": sad, "want_receipt": True})
    assert reply.kind == "publish"
    assert reply.artifact_said == schemer.said
    assert reply.want_receipt is True
    assert reply.attributes["schemaSaid"] == schemer.said
    assert reply.attributes["publisher"] == "EAlice"


def test_publish_rejects_non_schema_sad():
    # An ACDC-instance-shaped SAD (no $id / $schema) must be rejected.
    with pytest.raises(ValueError):
        schema_host_handler.validate_public_schema({"d": "Ex", "i": "Ey", "a": {}})


def test_publish_rejects_sad_missing_schema_key():
    # Has $id but not $schema — still not a valid JSON Schema.
    with pytest.raises(ValueError):
        schema_host_handler.validate_public_schema({"$id": "Eabc"})


def test_publish_rejects_structurally_invalid_schema():
    # Has $id and $schema but is structurally invalid (type must be a string).
    # validate_public_schema must surface this as ValueError, not a bare
    # kering.ValidationError or any other exception type.
    bad_schema = {"$id": "", "$schema": "http://json-schema.org/draft-07/schema#",
                  "type": 123}
    with pytest.raises(ValueError):
        schema_host_handler.validate_public_schema(bad_schema)


def test_publish_without_origin_sets_origin_none():
    schemer = _saidify(VALID_SCHEMA)
    sad = dict(schemer.sed)
    reply = TestRuntime(schema_host_handler.svc).send(
        route="/schema/cmd/publish", sender="EBob",
        payload={"schema": sad})
    assert reply.kind == "publish"
    assert reply.attributes["origin"] is None
    assert reply.want_receipt is False
