"""Synth assertions for ServiceAidFunction: two layers, layer-resident handler,
env merge, four-pattern LeadingKeys, IGrantable, cross-stack lock, inception CR."""
import json
import os
import tempfile

import aws_cdk as cdk
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_dynamodb as ddb
from aws_cdk.assertions import Template, Match

from keri_cdk import KeriCoreStack, ServiceAidFunction
from keri_cdk.framework_layer import ServiceAidFrameworkLayer
from keri_cdk.runtime_layer import KeriRuntimeLayer

ENV = cdk.Environment(account="111111111111", region="us-east-1")


def _asset():
    d = tempfile.mkdtemp()
    open(os.path.join(d, "gated_handler.py"), "w").close()
    return d


def _fw_asset():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "python"), exist_ok=True)
    open(os.path.join(d, "python", ".keep"), "w").close()
    return d


def _synth():
    app = cdk.App()
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=ENV)
    svc = cdk.Stack(app, "Svc", env=ENV)
    fn = ServiceAidFunction(
        svc, "Gated", alias="gated", core_table=core.table,
        compute_code=_lambda.Code.from_asset(_asset()),
        handler_ref="gated_handler:svc",
        runtime_layer=KeriRuntimeLayer(svc, "Rt", asset_path=_asset()),
        framework_layer=ServiceAidFrameworkLayer(svc, "Fw", asset_path=_fw_asset()),
        witnesses=[], toad=0, environment={"EXTRA": "1"})
    return Template.from_stack(svc), Template.from_stack(core), fn


def test_layer_resident_handler_string():
    svc, _, _ = _synth()
    svc.has_resource_properties("AWS::Lambda::Function", {
        "Handler": "keri_serviceaid.handler.handler",
        "Runtime": "python3.14", "Architectures": ["arm64"],
        "ReservedConcurrentExecutions": 1})


def test_two_layers_attached():
    svc, _, _ = _synth()
    body = json.dumps(svc.to_json())
    assert body.count("AWS::Lambda::LayerVersion") >= 2


def test_env_merge_keeps_framework_and_custom():
    svc, _, _ = _synth()
    svc.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {"Variables": Match.object_like({
            "SERVICEAID_ALIAS": "gated",
            "SERVICEAID_HANDLER": "gated_handler:svc",
            "EXTRA": "1"})}})


def test_four_pattern_leadingkeys():
    svc, _, _ = _synth()
    body = json.dumps(svc.to_json())
    assert "shared#*" in body and "__meta__#shared#*" in body
    assert "gated:*#*" in body and "__meta__#gated:*" in body


def test_cross_stack_core_table_lock():
    svc, _, _ = _synth()
    assert "Fn::ImportValue" in json.dumps(svc.to_json())


def test_igrantable_grant_principal_delegates():
    _, _, fn = _synth()
    # jsii creates new Python wrapper objects on each property access, so 'is'
    # identity is never stable — compare by jsii object reference instead.
    assert (fn.grant_principal.__jsii_ref__.ref
            == fn.function.grant_principal.__jsii_ref__.ref)


def test_grant_read_data_targets_function_role():
    app = cdk.App()
    core = KeriCoreStack(app, "Core2", table_name="keri-core", env=ENV)
    svc = cdk.Stack(app, "Svc2", env=ENV)
    fn = ServiceAidFunction(
        svc, "Gated", alias="gated", core_table=core.table,
        compute_code=_lambda.Code.from_asset(_asset()),
        handler_ref="gated_handler:svc",
        runtime_layer=KeriRuntimeLayer(svc, "Rt", asset_path=_asset()),
        framework_layer=ServiceAidFrameworkLayer(svc, "Fw", asset_path=_fw_asset()))
    lookup = ddb.Table(svc, "Lookup", partition_key=ddb.Attribute(
        name="pk", type=ddb.AttributeType.STRING))
    lookup.grant_read_data(fn)   # IGrantable payoff
    tmpl = Template.from_stack(svc)
    tmpl.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {
        "Statement": Match.array_with([Match.object_like({
            "Action": Match.array_with(["dynamodb:GetItem"])})])}})


def test_api_gateway_cesr_binary_media():
    svc, _, _ = _synth()
    svc.has_resource_properties("AWS::ApiGateway::RestApi", {
        "BinaryMediaTypes": Match.array_with(["application/cesr"])})
