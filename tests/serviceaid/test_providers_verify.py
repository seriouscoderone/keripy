"""OracleVerifier asserts the assurance tier of the sender's resolved key state."""
import pytest

from keri_serviceaid import OracleVerifier, VerificationError, KeyState


class FakeKever:
    def __init__(self, sn=0, wits=None):
        self.sn = sn
        self.wits = wits or []


class FakeDb:
    """Stands in for hby.db: .wigs.getLast returns truthy iff receipts exist."""
    def __init__(self, has_wigs):
        self._has = has_wigs

    class _Wigs:
        def __init__(self, has):
            self._has = has

        def getLast(self, keys=None):
            return b"sig" if self._has else None

    @property
    def wigs(self):
        return self._Wigs(self._has)


class FakeHby:
    def __init__(self, sender, has_wigs, wits):
        self.kevers = {sender: FakeKever(wits=wits)}
        self.db = FakeDb(has_wigs)


def test_unknown_sender_raises():
    hby = FakeHby("EOther", has_wigs=False, wits=[])
    with pytest.raises(VerificationError, match="no key state"):
        OracleVerifier(tier="signed").verify("EReq", b"", hby)


def test_signed_tier_accepts_any_known_kever():
    hby = FakeHby("EReq", has_wigs=False, wits=[])
    ks = OracleVerifier(tier="signed").verify("EReq", b"", hby)
    assert isinstance(ks, KeyState) and ks.pre == "EReq" and ks.tier == "signed"


def test_receipts_tier_requires_witness_receipts():
    # witnessed AID with no receipts in the oracle ⇒ tier unmet
    hby = FakeHby("EReq", has_wigs=False, wits=["EWit"])
    with pytest.raises(VerificationError, match="receipts"):
        OracleVerifier(tier="receipts").verify("EReq", b"", hby)


def test_receipts_tier_accepts_when_receipts_present():
    hby = FakeHby("EReq", has_wigs=True, wits=["EWit"])
    ks = OracleVerifier(tier="receipts").verify("EReq", b"", hby)
    assert ks.tier == "receipts"


def test_receipts_tier_accepts_unwitnessed_aid():
    # an UNwitnessed AID (no wits) passes at tier 'receipts' without needing
    # receipts — the gate keys off `if wits`, not `if wits is not None`.
    hby = FakeHby("EReq", has_wigs=False, wits=[])
    ks = OracleVerifier(tier="receipts").verify("EReq", b"", hby)
    assert ks.pre == "EReq" and ks.tier == "receipts"


def test_watcher_tier_not_implemented():
    hby = FakeHby("EReq", has_wigs=True, wits=["EWit"])
    with pytest.raises(NotImplementedError):
        OracleVerifier(tier="watcher").verify("EReq", b"", hby)
