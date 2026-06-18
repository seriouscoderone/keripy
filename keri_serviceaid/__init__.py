"""keri_serviceaid — the declarative Service-AID developer framework.

Declare an entity (`svc = ServiceAid(...)`), map routes to functions
(`@svc.command`), and inject the cross-cutting concerns (authz, verification,
issuance, …) as swappable providers. The framework supplies all KERI plumbing.
Shipped in `ServiceAidFrameworkLayer`; the dev's `compute_code` asset imports
from here. No keripy import at top level so this stays cheap to import.
"""
from .contract import ServiceAid, Request, Reply, Command, TestRuntime
from .providers import (
    Authorizer, Allowlist,
    Verifier, OracleVerifier, VerificationError, KeyState,
    Resolver, OracleResolver, Endpoint,
    Issuer, IpexGrantIssuer, Context,
    Deliverer, PostmanDeliverer,
    IdempotencyStore, DynamoLedger,
)

__all__ = [
    "ServiceAid", "Request", "Reply", "Command", "TestRuntime",
    "Authorizer", "Allowlist",
    "Verifier", "OracleVerifier", "VerificationError", "KeyState",
    "Resolver", "OracleResolver", "Endpoint",
    "Issuer", "IpexGrantIssuer", "Context",
    "Deliverer", "PostmanDeliverer",
    "IdempotencyStore", "DynamoLedger",
]
