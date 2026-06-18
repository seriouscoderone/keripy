"""OracleResolver picks the highest-priority reachable endpoint via hab.endsFor."""
import pytest

from keri_serviceaid import OracleResolver, Endpoint


class FakeHab:
    def __init__(self, ends):
        self._ends = ends

    def endsFor(self, pre):
        return self._ends


class FakeHby:
    def __init__(self, hab):
        self.habs = {"EService": hab}


def _resolver():
    return OracleResolver()


def test_mailbox_role_preferred_over_witness():
    ends = {
        "mailbox": {"EMbx": {"https": "https://mailbox.keri.host"}},
        "witness": {"EWit": {"https": "https://wit.example"}},
    }
    hby = FakeHby(FakeHab(ends))
    ep = _resolver().resolve("EReq", hby)
    assert isinstance(ep, Endpoint)
    assert ep.role == "mailbox" and ep.eid == "EMbx"
    assert ep.url == "https://mailbox.keri.host"


def test_controller_preferred_over_mailbox():
    ends = {
        "controller": {"ECtrl": {"https": "https://ctrl.example"}},
        "mailbox": {"EMbx": {"https": "https://mailbox.keri.host"}},
    }
    ep = _resolver().resolve("EReq", FakeHby(FakeHab(ends)))
    assert ep.role == "controller" and ep.eid == "ECtrl"


def test_witness_fallback_when_only_role():
    ends = {"witness": {"EWit": {"http": "http://wit.example"}}}
    ep = _resolver().resolve("EReq", FakeHby(FakeHab(ends)))
    assert ep.role == "witness" and ep.url == "http://wit.example"


def test_no_endpoint_raises():
    with pytest.raises(LookupError, match="no reachable endpoint"):
        _resolver().resolve("EReq", FakeHby(FakeHab({})))
