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


def _dber(name="svc", namespace=None, shared_namespace=None, shared_stores=None):
    """A DynamoDBer with no live AWS resources — only the pure formatters
    are exercised, which never touch the client/table."""
    return DynamoDBer(name=name, stores={}, table_name="core",
                      client=None, table=None, namespace=namespace,
                      shared_namespace=shared_namespace, shared_stores=shared_stores)


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


@needs_moto
def test_witness_mailbox_reger_namespaces_isolated_on_one_table():
    """Witness (:kel), mailbox (:mbx) and a Service-AID Reger (:tel) namespaces on the
    SAME core table read only their own rows — the Phase C per-service isolation."""
    with mock_aws():
        wit = DynamoDBer.open(name="witness", stores=["kels."], region="us-east-1",
                              table_name="keri-core", namespace="KeriHostWitness:kel")
        mbx = DynamoDBer.open(name="mailbox", stores=["kels."], region="us-east-1",
                              table_name="keri-core", namespace="KeriHostMailbox:mbx")
        reg = DynamoDBer.open(name="gated", stores=["kels."], region="us-east-1",
                              table_name="keri-core", namespace="gated:tel")
        wsub = wit.env.open_db(b"kels.")
        msub = mbx.env.open_db(b"kels.")
        rsub = reg.env.open_db(b"kels.")
        wit.setVal(wsub, b"AID", b"witness-row")
        mbx.setVal(msub, b"AID", b"mailbox-row")
        reg.setVal(rsub, b"AID", b"reger-row")
        # Same subdb + same key, three namespaces — each isolated.
        assert wit.getVal(wsub, b"AID") == b"witness-row"
        assert mbx.getVal(msub, b"AID") == b"mailbox-row"
        assert reg.getVal(rsub, b"AID") == b"reger-row"
        # A key written only in the witness namespace is invisible to the others.
        wit.setVal(wsub, b"WITONLY", b"secret")
        assert mbx.getVal(msub, b"WITONLY") is None
        assert reg.getVal(rsub, b"WITONLY") is None
        wit.close()
        mbx.close()
        reg.close()


def test_nskey_routes_shared_store_to_shared_namespace():
    """A store in shared_stores routes to shared_namespace; others to the instance namespace."""
    db = _dber(name="svc", namespace="svc:kel",
               shared_namespace="shared", shared_stores={"kels."})
    kels = DynamoSubDb(name="kels.", table_name="core")
    habs = DynamoSubDb(name="habs.", table_name="core")
    assert db._pk(kels, b"AID") == f"shared#kels.#{_hex(b'AID')}"
    assert db._gsi_pk(kels) == "shared#kels."
    assert db._pk(habs, b"AID") == f"svc:kel#habs.#{_hex(b'AID')}"
    assert db._gsi_pk(habs) == "svc:kel#habs."


def test_nskey_backward_compatible_when_no_shared_args():
    """No shared args ⇒ every store uses the instance namespace (Phase C behavior)."""
    db = _dber(name="svc", namespace="svc:kel")
    kels = DynamoSubDb(name="kels.", table_name="core")
    assert db._pk(kels, b"AID") == f"svc:kel#kels.#{_hex(b'AID')}"


def test_meta_pk_of_shared_store_lands_in_shared_namespace():
    """A shared store's meta row PK uses the shared namespace; the version meta store stays private."""
    db = _dber(name="svc", namespace="svc:kel",
               shared_namespace="shared", shared_stores={"kels."})
    assert db._nskey("kels.") == "shared#kels."        # -> meta PK __meta__#shared#kels.
    assert db._nskey("__meta__") == "svc:kel#__meta__"  # version meta is per-service


def test_shared_namespace_rejects_hash():
    import pytest
    with pytest.raises(ValueError):
        _dber(name="svc", shared_namespace="bad#ns", shared_stores={"kels."})


def test_shared_kel_stores_is_public_subset_of_baser():
    from keri.app.lambding import BASER_STORES, SHARED_KEL_STORES
    baser = set(BASER_STORES)
    # (1) Only Baser stores are ever shared. This alone guarantees no Reger-only
    # confidential store (credential bodies / TEL events) can leak into the oracle,
    # since none of those are in BASER_STORES.
    assert set(SHARED_KEL_STORES) <= baser, "shared set must be a subset of BASER_STORES"
    # (2) Must NOT share the node-PRIVATE Baser stores: escrows, hab registry,
    # KRAM/challenge, OOBI queues, reply/endpoint config.
    node_private = {"habs.", "names.", "hbys.", "pses.", "pwes.", "ooes.", "udes.",
                    "ldes.", "ures.", "vres.", "exns.", "oobis.", "rpys.", "ends.",
                    "locs.", "ctyp.", "msgc."}
    assert set(SHARED_KEL_STORES).isdisjoint(node_private), "shared set leaks a node-private store"
    # Belt-and-suspenders: credential bodies / TEL stores are never shared (also
    # guaranteed by the subset check above, since these are not Baser stores).
    assert set(SHARED_KEL_STORES).isdisjoint({"creds.", "cmse.", "ccrd.", "tvts.", "tels."}), \
        "credential-body / TEL store must never be shared"
    # the verifiable key-event/receipt/key-state core IS shared
    assert {"kels.", "evts.", "fels.", "sigs.", "wigs.", "rcts.", "stts.", "ksns."} \
        <= set(SHARED_KEL_STORES)


@needs_moto
def test_shared_kel_oracle_cross_service_read_and_private_isolation():
    """Service A writes a counterparty KEL into the SHARED store; a separate
    service B reads it from `shared` (the oracle) — but B cannot see A's PRIVATE
    store rows."""
    from moto import mock_aws
    with mock_aws():
        a = DynamoDBer.open(name="A", stores=["kels.", "habs."], region="us-east-1",
                            table_name="keri-core", namespace="A:kel",
                            shared_namespace="shared", shared_stores={"kels."})
        b = DynamoDBer.open(name="B", stores=["kels.", "habs."], region="us-east-1",
                            table_name="keri-core", namespace="B:kel",
                            shared_namespace="shared", shared_stores={"kels."})
        a_kels, b_kels = a.env.open_db(b"kels."), b.env.open_db(b"kels.")
        a_habs, b_habs = a.env.open_db(b"habs."), b.env.open_db(b"habs.")
        a.setVal(a_kels, b"EXcounterparty", b"key-event")     # A writes shared KEL
        assert b.getVal(b_kels, b"EXcounterparty") == b"key-event"  # B reads via oracle
        a.setVal(a_habs, b"AownHab", b"secret")               # A writes PRIVATE store
        assert b.getVal(b_habs, b"AownHab") is None            # invisible to B
        a.close(); b.close()
