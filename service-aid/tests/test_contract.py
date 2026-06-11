import pytest
from serviceaid.contract import Service, Request, Reply, TestRuntime


def test_command_registers_by_route():
    svc = Service()

    @svc.command(route="/rate/apply", issues="ESchemaSaid")
    def rate(req: Request) -> Reply:
        return Reply.acdc(recipient=req.sender, attributes={"score": 42})

    cmd = svc.lookup("/rate/apply")
    assert cmd is not None
    assert cmd.issues == "ESchemaSaid"
    assert cmd.fn is rate


def test_lookup_unknown_route_returns_none():
    assert Service().lookup("/nope") is None


def test_register_schema_returns_said_and_queues():
    svc = Service()
    sad = {"$id": "", "$schema": "http://json-schema.org/draft-07/schema#",
           "title": "T", "type": "object",
           "properties": {"a": {"type": "string"}}, "required": ["a"]}
    said = svc.register_schema(sad)
    assert said.startswith("E")          # SAID is a Blake3 digest
    assert len(svc.schemas) == 1
    assert svc.schemas[0]["$id"] == said  # saidified in place
    assert sad["$id"] == ""               # caller's dict untouched


def test_duplicate_route_rejected():
    svc = Service()

    @svc.command(route="/r", issues="E1")
    def a(req):
        return Reply.none()

    with pytest.raises(ValueError, match="duplicate route"):
        @svc.command(route="/r", issues="E2")
        def b(req):
            return Reply.none()


def test_reply_constructors():
    r = Reply.acdc(recipient="Erecip", attributes={"score": 1}, edges={"x": "y"})
    assert r.kind == "acdc" and r.recipient == "Erecip"
    assert r.attributes == {"score": 1} and r.edges == {"x": "y"}
    assert Reply.none().kind == "none"
    assert Reply.reject(reason="nope").kind == "reject"
    assert Reply.reject(reason="nope").reason == "nope"


def test_request_now_is_iso8601():
    req = Request(sender="Eabc", payload={}, credentials=[],
                  message_said="EmsgX", payload_said="EpayX", route="/r")
    assert "T" in req.now() and req.now().endswith("+00:00")


def test_testruntime_dispatches_and_captures():
    svc = Service()

    @svc.command(route="/rate/apply", issues="ESchemaSaid")
    def rate(req: Request) -> Reply:
        return Reply.acdc(recipient=req.sender,
                          attributes={"score": req.payload["x"] * 2})

    rt = TestRuntime(svc)
    reply = rt.send(route="/rate/apply", sender="Ecaller", payload={"x": 21})
    assert reply.kind == "acdc"
    assert reply.attributes == {"score": 42}
