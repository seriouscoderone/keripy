"""Synth-level assertions for the Gated Retrieval example app.

Mirrors examples/gated_retrieval/app.py (instantiating the stacks directly with a
concrete env so the cross-stack core-table reference resolves to a real
Fn::ImportValue). Asserts the example wires up: (1) the ServiceAid zip Lambda,
(2) the cross-stack core-table export (the lifecycle lock).
"""
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match

from keri_cdk import KeriCoreStack, ServiceAid

ENV = cdk.Environment(account="111111111111", region="us-east-1")


def _synth_example():
    app = cdk.App()
    core = KeriCoreStack(app, "KeriCore", table_name="keri-core", env=ENV)
    svc = cdk.Stack(app, "GatedRetrieval", env=ENV)
    ServiceAid(svc, "Gated", alias="gated", core_table=core.table,
               handler_module="gated_handler", allowlist=[])
    svc.add_dependency(core)
    return Template.from_stack(svc), Template.from_stack(core)


def test_gated_example_serviceaid_lambda_present():
    svc, _ = _synth_example()
    svc.has_resource_properties("AWS::Lambda::Function", {
        "Architectures": ["arm64"],
        "Runtime": "python3.14",
        "ReservedConcurrentExecutions": 1,
        "Handler": "handler.handler",
        "Layers": Match.any_value(),
    })


def test_gated_example_core_table_cross_stack_export():
    _, core = _synth_example()
    core.has_output("*", {"Export": Match.any_value()})
