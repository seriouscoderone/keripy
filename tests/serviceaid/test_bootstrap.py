def test_bootstrap_exposes_handler():
    # The serviceaid Lambda entry shim relocated into the keri_cdk library
    # (CDK Phase B): bootstrap.py now exposes ensure_libsodium() and the Lambda
    # entrypoint is handler.handler. Assert both: the libsodium shim is callable
    # and the handler entrypoint it guards is a callable.
    from keri_cdk.handlers.serviceaid import bootstrap, handler
    assert callable(bootstrap.ensure_libsodium)
    assert callable(handler.handler)
