"""KERI Service AID framework — wrap a Python function as an autonomous KERI principal.

The runtime/contract modules now live in the ``keri_cdk.handlers.serviceaid``
library (relocated for the CDK Phase B refactor). This package re-exports the
developer-facing contract symbols so ``from serviceaid import service`` keeps
working for handler modules, and ``serviceaid.cdk`` continues to provide the CDK
constructs.
"""
from keri_cdk.handlers.serviceaid.contract import service, Service, Request, Reply  # noqa: F401

__all__ = ["service", "Service", "Request", "Reply"]
