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


def test_edge_source_omits_operator_when_absent():
    source = _build_edge_source({"application": {"cred_said": "E1", "schema_said": "E2"}})
    assert "o" not in source["application"]
