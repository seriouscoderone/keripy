# -*- encoding: utf-8 -*-
"""Tenant-namespacing tests for DynamoDBer key formatters.

Greenfield: every key is namespaced — there is NO bare/legacy format. When no
explicit namespace is given, the instance name IS the namespace.
"""
import pytest

try:
    from moto import mock_aws
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from keri.db.dynamodbing import DynamoDBer, DynamoSubDb, _hex

needs_moto = pytest.mark.skipif(not HAS_MOTO, reason="requires moto")


def _dber(name="svc", namespace=None):
    """A DynamoDBer with no live AWS resources — only the pure formatters
    are exercised, which never touch the client/table."""
    return DynamoDBer(name=name, stores={}, table_name="core",
                      client=None, table=None, namespace=namespace)


def test_pk_defaults_namespace_to_name():
    """No explicit namespace ⇒ the instance name is the namespace. There is no
    bare `{subdb}#{key}` format anymore."""
    db = _dber(name="svc", namespace=None)
    sub = DynamoSubDb(name="kels.", table_name="core")
    assert db._pk(sub, b"AID") == f"svc#kels.#{_hex(b'AID')}"
    assert db._gsi_pk(sub) == "svc#kels."
    # "" is falsy ⇒ treated identically to None (falls back to name).
    assert _dber(name="svc", namespace="")._pk(sub, b"AID") == f"svc#kels.#{_hex(b'AID')}"


def test_pk_explicit_namespace_overrides_name():
    db = _dber(name="svc", namespace="rating:kel")
    sub = DynamoSubDb(name="kels.", table_name="core")
    assert db._pk(sub, b"AID") == f"rating:kel#kels.#{_hex(b'AID')}"
    assert db._gsi_pk(sub) == "rating:kel#kels."


@needs_moto
def test_two_namespaces_in_one_table_are_isolated():
    """Same subdb + same key under two explicit namespaces must not collide."""
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
def test_name_default_namespace_isolated_from_explicit():
    """A name-defaulted instance (namespace == name) shares no keys with an
    explicitly-namespaced instance whose namespace differs from that name."""
    with mock_aws():
        named = DynamoDBer.open(name="w", stores=["kels."], region="us-east-1",
                                table_name="shared-core")  # namespace -> "w"
        ns = DynamoDBer.open(name="w", stores=["kels."], region="us-east-1",
                             table_name="shared-core", namespace="rating:kel")
        nsub = named.env.open_db(b"kels.")
        esub = ns.env.open_db(b"kels.")
        named.setVal(nsub, b"k", b"from-w")
        assert ns.getVal(esub, b"k") is None     # "w#..." vs "rating:kel#..."
        named.close()
        ns.close()


def test_namespace_with_hash_rejected():
    """'#' in namespace would break key-encoding injectivity — must raise."""
    with pytest.raises(ValueError):
        _dber(namespace="bad#ns")


def test_name_with_hash_rejected():
    """The '#' guard also applies to a name-derived namespace."""
    with pytest.raises(ValueError):
        _dber(name="bad#name", namespace=None)


@needs_moto
def test_clear_store_is_namespace_scoped():
    """_clear_store on one namespace must not wipe siblings in the shared table."""
    with mock_aws():
        a = DynamoDBer.open(name="svc", stores=["kels."], region="us-east-1",
                            table_name="shared-core", namespace="a:kel")
        b = DynamoDBer.open(name="svc", stores=["kels."], region="us-east-1",
                            table_name="shared-core", namespace="b:kel")
        named = DynamoDBer.open(name="svc", stores=["kels."], region="us-east-1",
                                table_name="shared-core")  # namespace -> "svc"
        for db in (a, b, named):
            sub = db.env.open_db(b"kels.")
            db.setVal(sub, b"k", b"v")
        a._clear_store("kels.")
        assert a.getVal(a.env.open_db(b"kels."), b"k") is None
        assert b.getVal(b.env.open_db(b"kels."), b"k") == b"v"
        assert named.getVal(named.env.open_db(b"kels."), b"k") == b"v"
        b.close()
        named.close()
        a.close()
