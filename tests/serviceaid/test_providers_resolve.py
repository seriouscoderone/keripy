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
    # the recipient the reply is addressed to (drives Poster.dest at delivery)
    assert ep.cid == "EReq"


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


# ---------------------------------------------------------------------------
# BoundResolver
# ---------------------------------------------------------------------------
from keri_serviceaid import BoundResolver, Endpoint as _Endpoint


class FakeBoundHab:
    """Minimal hab stub exposing endsFor(pre) -> {role: {eid: {scheme: url}}}."""
    def __init__(self, ends):
        self._ends = ends

    def endsFor(self, pre):
        return self._ends


def test_bound_resolver_picks_highest_priority_role_https():
    hab = FakeBoundHab({
        "witness": {"Ewit": {"http": "http://wit/"}},
        "mailbox": {"Embx": {"https": "https://mbx/", "http": "http://mbx/"}},
    })
    ep = BoundResolver(hab).resolve("Esender", hby=None)
    assert ep == _Endpoint(role="mailbox", eid="Embx", url="https://mbx/", cid="Esender")


def test_bound_resolver_raises_when_no_endpoint():
    with pytest.raises(LookupError):
        BoundResolver(FakeBoundHab({})).resolve("Esender", hby=None)
