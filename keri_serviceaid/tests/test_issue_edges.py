"""Unit tests for the ACDC edge-source builder in keri_serviceaid/providers/issue.py.

ACDC's default unary edge operator is I2I; a chained edge that must instead be
verified NI2I (or any other non-default operator) needs its `o` explicitly
present in the edge source block. These tests pin the contract:

- `op` present on the edge def  -> `o` present in the built source.
- `op` absent from the edge def -> `o` absent from the built source (default
  I2I semantics apply implicitly, per ACDC spec).
"""
from keri_serviceaid.providers.issue import _build_edge_source


def test_edge_source_includes_operator():
    edges = {"application": {"cred_said": "ENODESAID", "schema_said": "ESCHEMASAID", "op": "NI2I"}}
    source = _build_edge_source(edges)
    assert source["application"] == {"n": "ENODESAID", "s": "ESCHEMASAID", "o": "NI2I"}


def test_edge_source_includes_operator_under_operator_key():
    """Regression (live-demo blocker): Locksmith's issue dialog + legacy
    build_edges_block emit the operator under the key `operator`, not `op`.
    Reading only `op` dropped `o`, producing an edge a NI2I-required schema
    (carrier_license's `application` edge) rejected with "'o' is a required
    property". Both keys must yield `o`."""
    edges = {"application": {"cred_said": "EN", "schema_said": "ES", "operator": "NI2I"}}
    source = _build_edge_source(edges)
    assert source["application"] == {"n": "EN", "s": "ES", "o": "NI2I"}


def test_edge_source_omits_operator_when_absent():
    source = _build_edge_source({"application": {"cred_said": "E1", "schema_said": "E2"}})
    assert "o" not in source["application"]
