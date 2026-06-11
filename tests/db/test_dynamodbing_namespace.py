# -*- encoding: utf-8 -*-
"""Tenant-namespacing tests for DynamoDBer key formatters."""
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri.db.dynamodbing import DynamoDBer, DynamoSubDb, _hex

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def _bare_dber(namespace=""):
    """A DynamoDBer with no live AWS resources — only the pure formatters
    are exercised, which never touch the client/table."""
    return DynamoDBer(name="svc", stores={}, table_name="core",
                      client=None, table=None, namespace=namespace)


def test_pk_legacy_no_namespace_unchanged():
    db = _bare_dber(namespace="")
    sub = DynamoSubDb(name="kels.", table_name="core")
    assert db._pk(sub, b"AID") == f"kels.#{_hex(b'AID')}"
    assert db._gsi_pk(sub) == "kels."


def test_pk_namespaced():
    db = _bare_dber(namespace="rating:kel")
    sub = DynamoSubDb(name="kels.", table_name="core")
    assert db._pk(sub, b"AID") == f"rating:kel#kels.#{_hex(b'AID')}"
    assert db._gsi_pk(sub) == "rating:kel#kels."


@needs_moto
def test_two_namespaces_in_one_table_are_isolated():
    """Same subdb + same key under two namespaces must not collide."""
    with mock_aws():
        kel = DynamoDBer.open(name="svc", stores=["kels."], region="us-east-1",
                              table_name="shared-core", namespace="rating:kel")
        tel = DynamoDBer.open(name="svc", stores=["kels."], region="us-east-1",
                              table_name="shared-core", namespace="rating:tel")
        ksub = kel.env.open_db(b"kels.")
        tsub = tel.env.open_db(b"kels.")
        kel.setVal(ksub, b"k", b"from-kel")
        tel.setVal(tsub, b"k", b"from-tel")
        assert kel.getVal(ksub, b"k") == b"from-kel"
        assert tel.getVal(tsub, b"k") == b"from-tel"  # not overwritten
        kel.close()
        tel.close()


@needs_moto
def test_legacy_namespace_still_isolated_from_namespaced():
    """An un-namespaced (legacy) instance shares no keys with a namespaced one."""
    with mock_aws():
        legacy = DynamoDBer.open(name="w", stores=["kels."], region="us-east-1",
                                 table_name="shared-core")
        ns = DynamoDBer.open(name="w", stores=["kels."], region="us-east-1",
                             table_name="shared-core", namespace="rating:kel")
        lsub = legacy.env.open_db(b"kels.")
        nsub = ns.env.open_db(b"kels.")
        legacy.setVal(lsub, b"k", b"legacy")
        assert ns.getVal(nsub, b"k") is None
        legacy.close()
        ns.close()
