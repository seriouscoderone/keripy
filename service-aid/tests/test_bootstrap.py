def test_bootstrap_exposes_handler():
    import importlib, sys, pathlib
    root = pathlib.Path(__file__).resolve().parents[1]   # service-aid/
    sys.path.insert(0, str(root))
    mod = importlib.import_module("bootstrap")
    assert callable(mod.handler)
