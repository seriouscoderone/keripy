def test_bootstrap_exposes_handler():
    import importlib, sys, pathlib
    # service-aid/ holds bootstrap.py (the Lambda entry shim). This test moved
    # to tests/serviceaid/, so walk up to the repo root and into service-aid/.
    root = pathlib.Path(__file__).resolve().parents[2] / "service-aid"
    sys.path.insert(0, str(root))
    mod = importlib.import_module("bootstrap")
    assert callable(mod.handler)
