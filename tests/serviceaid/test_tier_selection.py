import pytest
from keri_serviceaid import ServiceAid, OracleVerifier, KeyState, VerificationError
from keri_serviceaid.providers.verify import TIER_ORDER, max_tier


class FakeKever:
    def __init__(self, sn=0, wits=None):
        self.sn = sn
        self.wits = wits or []


class FakeDb:
    def __init__(self, has_wigs):
        self._has = has_wigs
        self.wigs = self  # so hby.db.wigs.getLast works

    def getLast(self, keys):
        return b"sig" if self._has else None


class FakeHby:
    def __init__(self, sender, *, has_wigs=False, wits=None):
        self.kevers = {sender: FakeKever(sn=3, wits=wits)}
        self.db = FakeDb(has_wigs)


def test_tier_order_and_max():
    assert TIER_ORDER["signed"] < TIER_ORDER["receipts"] < TIER_ORDER["watcher"]
    assert max_tier("signed", None) == "signed"
    assert max_tier("signed", "receipts") == "receipts"
    assert max_tier("receipts", "signed") == "receipts"


def test_min_tier_raises_effective_tier():
    # deploy floor 'signed', command demands 'receipts', witnessed but no receipt -> drop
    hby = FakeHby("EReq", has_wigs=False, wits=["EWit"])
    with pytest.raises(VerificationError, match="receipts"):
        OracleVerifier(tier="signed").verify("EReq", b"", hby, min_tier="receipts")


def test_min_tier_met_returns_effective_tier():
    hby = FakeHby("EReq", has_wigs=True, wits=["EWit"])
    ks = OracleVerifier(tier="signed").verify("EReq", b"", hby, min_tier="receipts")
    assert isinstance(ks, KeyState) and ks.tier == "receipts" and ks.sn == 3


def test_unknown_min_tier_rejected():
    hby = FakeHby("EReq")
    with pytest.raises(ValueError, match="unknown"):
        OracleVerifier(tier="signed").verify("EReq", b"", hby, min_tier="bogus")


def test_command_records_min_assurance_tier():
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/rate", issues="Equote", min_assurance_tier="receipts")
    def rate(req):
        ...

    cmd = svc.lookup("/rate")
    assert cmd.min_assurance_tier == "receipts"


def test_command_min_assurance_tier_defaults_none():
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/ping")
    def ping(req):
        ...

    assert svc.lookup("/ping").min_assurance_tier is None


def test_command_unknown_min_assurance_tier_rejected():
    svc = ServiceAid(alias="rating-engine")

    with pytest.raises(ValueError):
        @svc.command(route="/rate", issues="Equote", min_assurance_tier="platinum")
        def rate(req):
            ...
