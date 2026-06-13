import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import WitnessStack


def _synth():
    app = cdk.App()
    s = WitnessStack(app, "Wit", name="witness-test", alias="witness",
                     domain_name="witness.example.com", hosted_zone_id="Z123ABC456DEF7",
                     witness_url="https://witness.example.com")
    return Template.from_stack(s)


def test_witness_zip_arm64_reserved_concurrency_layer():
    t = _synth()
    t.has_resource_properties("AWS::Lambda::Function", {
        "Architectures": ["arm64"],
        "ReservedConcurrentExecutions": 1,
        "Handler": "witness_handler.handler",
        "Runtime": "python3.14",
        "Layers": Match.any_value(),
        "Environment": {"Variables": Match.object_like({"LD_LIBRARY_PATH": "/opt/lib"})},
    })


def test_witness_owns_baser_table_with_gsi():
    t = _synth()
    t.resource_count_is("AWS::DynamoDB::Table", 1)
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "GlobalSecondaryIndexes": [{"IndexName": "subdb-index"}]})


def test_witness_scoped_secretsmanager_iam():
    t = _synth()
    t.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {"Statement": Match.array_with([Match.object_like({
            "Action": Match.array_with(["secretsmanager:GetSecretValue"])})])}})
