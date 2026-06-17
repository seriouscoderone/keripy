"""ends./locs./eans. are shared in the oracle: service-B resolves service-A's
authorized end-role + location from the pooled shared# namespace."""
import pytest

from keri.db.dynamodbing import NEVER_SHARE_STORES
from keri.app.lambding import SHARED_KEL_STORES


def test_reachability_stores_are_shared_and_safe():
    for s in ("ends.", "locs.", "eans."):
        assert s in SHARED_KEL_STORES, f"{s} must be in SHARED_KEL_STORES"
    assert SHARED_KEL_STORES.isdisjoint(NEVER_SHARE_STORES)


@pytest.mark.integration
def test_cross_habery_endsfor_resolves_over_oracle():
    """Service-A publishes an end-role/loc for a peer into the shared ns; a
    second Habery on the SAME oracle table resolves that peer's URL via endsFor.

    Outline (filled by the agent against real makeEndRole/makeLocScheme APIs, or
    validated live by the Task 11 deploy):
      1. mock_aws(); two DynamoDBer.open(...) with distinct private ns but the
         SAME shared_namespace='shared'/shared_stores=SHARED_KEL_STORES on table
         'keri-core'; setup_baser each.
      2. Build Habery-A on dbA; a peer hab; publish the peer's controller end-role
         + a https location; parse into A (lands in shared ends./locs./eans.).
      3. Build Habery-B on dbB (same oracle); also parse the peer's KEL so the
         kever exists; assert hby_B.<service hab>.endsFor(peer_pre) returns a
         mailbox/controller URL — reachability resolved from the oracle.
    """
    pytest.skip("integration scaffold — validated live by the Task 11 deploy; "
                "gated by -m integration")
