"""The reworked gated example: ≥2 routes on the ServiceAid + a synthesizing app."""
import importlib
import os
import sys

import pytest


@pytest.fixture
def gated_module():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, "examples", "gated_retrieval")
    sys.path.insert(0, path)
    sys.modules.pop("gated_handler", None)
    mod = importlib.import_module("gated_handler")
    yield mod
    sys.path.remove(path)
    sys.modules.pop("gated_handler", None)


def test_gated_svc_has_two_routes(gated_module):
    svc = gated_module.svc
    assert set(svc.routes) == {"/gated/cmd/request_record", "/gated/cmd/revoke_record"}


def test_request_record_returns_acdc(gated_module):
    from keri_serviceaid import TestRuntime
    reply = TestRuntime(gated_module.svc).send(
        route="/gated/cmd/request_record", sender="EReq", payload={"recordId": "r1"})
    assert reply.kind == "acdc" and reply.recipient == "EReq"
    assert reply.attributes["recordId"] == "r1"


def test_revoke_record_returns_none(gated_module):
    from keri_serviceaid import TestRuntime
    reply = TestRuntime(gated_module.svc).send(
        route="/gated/cmd/revoke_record", sender="EReq", payload={"recordId": "r1"})
    assert reply.kind == "none"


def test_app_imports_and_builds():
    """Running app.py builds the stacks + calls app.synth() without raising.

    The two layer asset dirs (keri_runtime/, serviceaid_framework/) are gitignored
    build artifacts. If they don't exist, Code.from_asset raises at synth time.
    We create minimal placeholder dirs (a python/ subdir each) so CDK can stage
    them. These dirs are gitignored so they will not be committed."""
    import pathlib
    import shutil

    root = pathlib.Path(__file__).resolve().parents[2]

    # Ensure layer placeholder dirs exist (gitignored; CDK needs them to synth).
    # Track which we create so we can remove only those in the finally block,
    # leaving any real built layer assets untouched.
    keri_runtime_dir = root / "keri_cdk" / "layers" / "keri_runtime"
    framework_dir = root / "keri_cdk" / "layers" / "serviceaid_framework"
    created = []
    for layer_dir in (keri_runtime_dir, framework_dir):
        if not layer_dir.exists():
            created.append(layer_dir)
        (layer_dir / "python").mkdir(parents=True, exist_ok=True)

    path = str(root / "examples" / "gated_retrieval")
    sys.path.insert(0, path)
    sys.modules.pop("app", None)
    sys.modules.pop("gated_handler", None)
    try:
        import app  # noqa: F401 — importing runs the module: builds stacks + synth()
    finally:
        sys.path.remove(path)
        sys.modules.pop("app", None)
        sys.modules.pop("gated_handler", None)
        for layer_dir in created:   # remove only the placeholders we created
            shutil.rmtree(layer_dir, ignore_errors=True)
