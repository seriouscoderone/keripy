"""keri_serviceaid — the declarative Service-AID developer framework.

Declare an entity (`svc = ServiceAid(...)`), map routes to functions
(`@svc.command`), and inject the cross-cutting concerns (authz, verification,
issuance, …) as swappable providers. The framework supplies all KERI plumbing.
Shipped in `ServiceAidFrameworkLayer`; the dev's `compute_code` asset imports
from here. No keripy import at top level so this stays cheap to import.
"""
from .contract import ServiceAid, Request, Reply, Command, TestRuntime, CredentialReq
from .providers import (
    Authorizer, Allowlist,
    CredentialGate,
    Verifier, OracleVerifier, VerificationError, KeyState,
    Resolver, OracleResolver, BoundResolver, Endpoint,
    Issuer, IpexGrantIssuer, Context,
    Deliverer, PostmanDeliverer,
    IdempotencyStore, DynamoLedger, LMDBLedger,
)
from .local_runtime import LocalRuntime, LocalState, LocalCfg

__all__ = [
    "ServiceAid", "Request", "Reply", "Command", "TestRuntime", "CredentialReq",
    "Authorizer", "Allowlist",
    "CredentialGate",
    "Verifier", "OracleVerifier", "VerificationError", "KeyState",
    "Resolver", "OracleResolver", "BoundResolver", "Endpoint",
    "Issuer", "IpexGrantIssuer", "Context",
    "Deliverer", "PostmanDeliverer",
    "IdempotencyStore", "DynamoLedger", "LMDBLedger",
    "LocalRuntime", "LocalState", "LocalCfg",
]
