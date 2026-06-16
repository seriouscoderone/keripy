"""Synth-level assertions for the ServiceAid construct (Phase B).

Asserts: (1) the service Lambda is a python3.14/arm64 zip on the KeriRuntimeLayer
with reserved-concurrency=1; (2) passing a core table from a DIFFERENT stack
emits the cross-stack Export/Fn::ImportValue lock on the owning stack; (3) the
multi-tenant dynamodb:LeadingKeys IAM scoping is present.
"""
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match

from keri_cdk import KeriCoreStack, ServiceAid

# A fixed dummy account/region: both the core stack and the service stack need a
# concrete env so the cross-stack reference resolves to a real Fn::ImportValue
# (not an unresolved token).
ENV = cdk.Environment(account="111111111111", region="us-east-1")


def _synth():
    app = cdk.App()
    svc = cdk.Stack(app, "Svc", env=ENV)
    # The core table is OWNED by a different stack; passing its .table across the
    # stack boundary is what emits the cross-stack lock.
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=ENV)
    ServiceAid(svc, "Gated", alias="gated", core_table=core.table,
               handler_module="gated_handler", allowlist=["EReqAID"])
    return Template.from_stack(svc), Template.from_stack(core)


def test_service_aid_zip_layer_reserved_concurrency():
    svc, _ = _synth()
    svc.has_resource_properties("AWS::Lambda::Function", {
        "Architectures": ["arm64"], "Runtime": "python3.14",
        "ReservedConcurrentExecutions": 1, "Layers": Match.any_value()})


def test_core_table_export_creates_cross_stack_lock():
    _, core = _synth()
    core.has_output("*", {"Export": Match.any_value()})


def test_service_aid_leadingkeys_iam_present():
    svc, _ = _synth()
    svc.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {"Statement":
        Match.array_with([Match.object_like({"Condition": Match.object_like({
            "ForAllValues:StringLike": Match.any_value()})})])}})


def test_service_side_imports_core_table_lock():
    """The SERVICE stack must contain an Fn::ImportValue for the core table.

    The exporting side (core stack) is covered by
    test_core_table_export_creates_cross_stack_lock.  This test covers the
    consuming side so that a regression that drops the cross-stack reference
    (e.g. inlining the table ARN as a literal) is caught before it silently
    removes the lifecycle lock.
    """
    import json
    svc, _ = _synth()
    body = json.dumps(svc.to_json())
    assert "Fn::ImportValue" in body, (
        "service stack does not import the core table — cross-stack lifecycle "
        "lock is broken (the core stack could be deleted while the service is live)"
    )


def test_service_aid_grants_shared_leadingkeys():
    import json
    svc, _core = _synth()
    body = json.dumps(svc.to_json())
    assert "shared#*" in body and "__meta__#shared#*" in body, \
        "Service-AID must grant the shared-KEL oracle namespace"
