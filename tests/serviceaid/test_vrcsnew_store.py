"""vrcsNew (v2 indexed transferable-receipt store) is registered in the DynamoDBer
store-set so witnesses on the v2 base have somewhere to write it, but it stays a
per-witness WRITE-LOG — never pooled into the shared-KEL oracle (SHARED_KEL_STORES),
because pooling receipt logs collapses all witnesses' receipts to one and breaks
keri.app.agenting.Receiptor toad convergence."""
from moto import mock_aws

from keri.db.dynamodbing import DynamoDBer
from keri.app.lambding import BASER_STORES, SHARED_KEL_STORES


def test_vrcsnew_registered_and_node_private():
    assert "vrcsnew." in BASER_STORES, "vrcsNew must be in the DynamoDBer store-set"
    assert "vrcsnew." not in SHARED_KEL_STORES, (
        "vrcsNew is a per-witness receipt write-log; pooling it breaks toad convergence")
    assert "vrcs." not in SHARED_KEL_STORES              # invariant unchanged
    # receipt/event write-logs stay node-private as a set
    assert {"vrcs.", "vrcsnew.", "wigs.", "rcts."}.isdisjoint(SHARED_KEL_STORES)


def test_dynamodber_creates_vrcsnew_handle():
    with mock_aws():
        db = DynamoDBer.open(name="wit", stores=BASER_STORES,
                             table_name="keri-core", region="us-east-1")
        assert any("vrcsnew" in name for name in db._stores), (
            "DynamoDBer did not create a handle for the vrcsnew. store")
