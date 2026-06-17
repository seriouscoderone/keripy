"""keri_serviceaid public API surface must import and expose the v1 names."""


def test_package_imports():
    import keri_serviceaid  # noqa: F401


def test_public_names_present():
    import keri_serviceaid as ks
    for name in (
        "ServiceAid", "Request", "Reply", "Command", "TestRuntime",
        "Authorizer", "Allowlist",
        "Verifier", "OracleVerifier", "VerificationError", "KeyState",
        "Resolver", "OracleResolver", "Endpoint",
        "Issuer", "IpexGrantIssuer", "Context",
        "Deliverer", "PostmanDeliverer",
        "IdempotencyStore", "DynamoLedger",
    ):
        assert hasattr(ks, name), f"keri_serviceaid is missing {name}"
