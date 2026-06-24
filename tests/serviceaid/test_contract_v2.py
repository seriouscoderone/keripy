"""ServiceAid registry, Reply factories, route guards, TestRuntime."""
import pytest

from keri_serviceaid import ServiceAid, Reply, Request, TestRuntime


def _svc():
    return ServiceAid(alias="mvr-bureau")


def test_command_registration_and_lookup():
    svc = _svc()

    @svc.command(route="/mvr/cmd/request_record", issues="ESchemaSaid")
    def request_record(req: Request) -> Reply:
        return Reply.none()

    assert svc.routes == ["/mvr/cmd/request_record"]
    cmd = svc.lookup("/mvr/cmd/request_record")
    assert cmd.route == "/mvr/cmd/request_record"
    assert cmd.issues == "ESchemaSaid"
    assert cmd.payload_schema is None
    assert callable(cmd.fn)


def test_duplicate_route_raises():
    svc = _svc()

    @svc.command(route="/mvr/cmd/x")
    def a(req): return Reply.none()

    with pytest.raises(ValueError, match="duplicate route"):
        # the duplicate-route ValueError fires at DECORATION time, before b() runs
        @svc.command(route="/mvr/cmd/x")
        def b(req): return Reply.none()


def test_ipex_route_rejected():
    svc = _svc()
    with pytest.raises(ValueError, match="/ipex/"):
        @svc.command(route="/ipex/grant")
        def grant(req): return Reply.none()


def test_reply_factories():
    a = Reply.acdc(recipient="EReq", attributes={"vin": "1"})
    assert a.kind == "acdc" and a.recipient == "EReq" and a.attributes == {"vin": "1"}
    assert Reply.none().kind == "none"
    r = Reply.reject(reason="nope")
    assert r.kind == "reject" and r.reason == "nope"


def test_providers_stored_and_default_none():
    sentinel = object()
    svc = ServiceAid(alias="mvr-bureau", witnesses=["EWit"], toad=1, authz=sentinel)
    assert svc.alias == "mvr-bureau"
    assert svc.witnesses == ["EWit"]
    assert svc.toad == 1
    assert svc.authz is sentinel        # injected provider stored verbatim
    assert svc.verifier is None         # left None here; runtime wires the default


def test_testruntime_send_invokes_fn():
    svc = _svc()

    @svc.command(route="/mvr/cmd/echo")
    def echo(req: Request) -> Reply:
        return Reply.acdc(recipient=req.sender, attributes=req.payload)

    rt = TestRuntime(svc)
    reply = rt.send(route="/mvr/cmd/echo", sender="EReq", payload={"k": "v"})
    assert reply.kind == "acdc" and reply.recipient == "EReq"
    assert reply.attributes == {"k": "v"}


def test_testruntime_unknown_route_raises():
    rt = TestRuntime(_svc())
    with pytest.raises(KeyError):
        rt.send(route="/nope", sender="E", payload={})


def test_request_now_returns_iso8601_timestamp():
    req = Request(sender="EReq", route="/x", payload={})
    ts = req.now()
    assert isinstance(ts, str)
    # iso8601 with date + 'T' separator (e.g. 2026-06-17T...)
    assert "T" in ts and ts[:4].isdigit() and ts[4] == "-"


from keri_serviceaid import ServiceAid, CredentialReq


def test_command_records_requires_credential():
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/rate", issues="Equote",
                 requires_credential=CredentialReq(schema="Ebroker"))
    def rate(req):
        ...

    cmd = svc.lookup("/rate")
    assert cmd.requires_credential == CredentialReq(schema="Ebroker")
    assert cmd.requires_credential.presentation == "cache"
    assert cmd.requires_credential.cadence == "revocation-recheck"


def test_command_requires_credential_defaults_none():
    svc = ServiceAid(alias="rating-engine")

    @svc.command(route="/ping")
    def ping(req):
        ...

    assert svc.lookup("/ping").requires_credential is None
