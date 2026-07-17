"""Six extension-point Protocols + their default implementations.

Each module defines a typing.Protocol and one default impl. Adding a new
extension point = a new module here (new Protocol + default); the pipeline
never changes. See the framework design spec.
"""
from .authz import Authorizer, Allowlist
from .credgate import CredentialGate
from .verify import Verifier, OracleVerifier, VerificationError, KeyState
from .resolve import Resolver, OracleResolver, BoundResolver, Endpoint
from .issue import (
    Issuer, IpexGrantIssuer, Context,
    issue_credential, frame_grant_for, self_issue_and_grant,
)
from .deliver import Deliverer, PostmanDeliverer
from .idempotency import IdempotencyStore, DynamoLedger, LMDBLedger
from .admit import admit_grant

__all__ = [
    "Authorizer", "Allowlist",
    "CredentialGate",
    "Verifier", "OracleVerifier", "VerificationError", "KeyState",
    "Resolver", "OracleResolver", "BoundResolver", "Endpoint",
    "Issuer", "IpexGrantIssuer", "Context",
    "issue_credential", "frame_grant_for", "self_issue_and_grant",
    "Deliverer", "PostmanDeliverer",
    "IdempotencyStore", "DynamoLedger", "LMDBLedger",
    "admit_grant",
]
