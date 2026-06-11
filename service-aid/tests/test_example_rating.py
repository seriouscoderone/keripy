import importlib
import sys
import pathlib

from serviceaid.contract import service, TestRuntime


def test_rating_engine_scores_via_testruntime():
    service._commands.clear()
    service.schemas.clear()
    root = pathlib.Path(__file__).resolve().parents[1] / "examples" / "rating_engine"
    sys.path.insert(0, str(root))
    # Decorators run only on first import; reload if the module is cached.
    mod = sys.modules.get("handler")
    importlib.reload(mod) if mod else importlib.import_module("handler")

    rt = TestRuntime(service)
    reply = rt.send(route="/rate/apply", sender="Ecaller",
                    payload={"risk_profile": {"age": 30, "claims": 0}})
    assert reply.kind == "acdc"
    assert isinstance(reply.attributes["score"], (int, float))
    assert reply.recipient == "Ecaller"
