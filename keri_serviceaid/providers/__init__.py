"""Six extension-point Protocols + their default implementations.

Each module defines a typing.Protocol and one default impl. Adding a new
extension point = a new module here (new Protocol + default); the pipeline
never changes. See the framework design spec.
"""
from .authz import Authorizer, Allowlist
from .verify import Verifier, OracleVerifier, VerificationError, KeyState
from .resolve import Resolver, OracleResolver, Endpoint
from .issue import Issuer, IpexGrantIssuer, Context
from .deliver import Deliverer, PostmanDeliverer
from .idempotency import IdempotencyStore, DynamoLedger, LMDBLedger

__all__ = [
    "Authorizer", "Allowlist",
    "Verifier", "OracleVerifier", "VerificationError", "KeyState",
    "Resolver", "OracleResolver", "Endpoint",
    "Issuer", "IpexGrantIssuer", "Context",
    "Deliverer", "PostmanDeliverer",
    "IdempotencyStore", "DynamoLedger", "LMDBLedger",
]
