from keri.app import habbing
from keri.core import signing
from keri.kering import Schemes
from keri.recording import LocationRecord


def test_wss_loc_scheme_round_trips():
    assert getattr(Schemes, "wss", None) == "wss"          # scheme is registered
    with habbing.openHby(name="wsstest", temp=True) as hby:
        hab = hby.makeHab(name="svc")
        # Pin a wss loc directly (makeLocScheme builds a reply; it does not persist to db.locs).
        hab.db.locs.pin(keys=(hab.pre, Schemes.wss),
                        val=LocationRecord(url="wss://mailbox.example/prod"))
        assert hab.fetchUrl(hab.pre, scheme=Schemes.wss) == "wss://mailbox.example/prod"
        assert hab.fetchUrl(hab.pre, scheme=Schemes.https) is None   # absent scheme -> None (no loc record)
