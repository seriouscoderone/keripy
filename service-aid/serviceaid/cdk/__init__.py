"""CDK constructs for KERI Service AIDs (transitional shim).

The constructs moved into the ``keri_cdk`` library in CDK Phase B (Task 6):
``ServiceAid`` is now ``keri_cdk.service_aid`` and the inception Custom Resource
is ``keri_cdk._inception``. This module re-exports them from ``keri_cdk`` so
legacy imports (``from serviceaid.cdk import KeriCoreStack, ServiceAid``) keep
working until Task 10 deletes the service-aid/ tree.
"""
from keri_cdk import KeriCoreStack, ServiceAid  # noqa: F401

__all__ = ["KeriCoreStack", "ServiceAid"]
