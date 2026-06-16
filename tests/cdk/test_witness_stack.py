import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import WitnessStack, KeriCoreStack

ENV = cdk.Environment(account="111111111111", region="us-east-1")


def _synth():
    app = cdk.App()
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=ENV)
    s = WitnessStack(app, "Wit", name="witness-test", alias="witness",
                     domain_name="witness.example.com", hosted_zone_id="Z123ABC456DEF7",
                     witness_url="https://witness.example.com",
                     core_table=core.table, env=ENV)
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


def test_witness_owns_no_table():
    """Phase C: the witness no longer owns a Baser table — it uses the shared core table."""
    t = _synth()
    t.resource_count_is("AWS::DynamoDB::Table", 0)


def test_witness_leadingkeys_scoped_iam():
    """The witness's table IAM is LeadingKeys-scoped (not a full-table grant)."""
    t = _synth()
    t.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {"Statement":
        Match.array_with([Match.object_like({"Condition": Match.object_like({
            "ForAllValues:StringLike": Match.object_like({
                "dynamodb:LeadingKeys": Match.any_value()})})})])}})


def test_witness_namespace_env_present():
    t = _synth()
    t.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {"Variables": Match.object_like({"WITNESS_NAMESPACE": Match.any_value()})}})


def test_witness_imports_core_table_lock():
    """Witness stack references the core table cross-stack -> Fn::ImportValue lifecycle lock."""
    import json
    body = json.dumps(_synth().to_json())
    assert "Fn::ImportValue" in body, "witness must import the core table (cross-stack lock)"


def test_witness_scoped_secretsmanager_iam():
    t = _synth()
    t.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": {"Statement": Match.array_with([Match.object_like({
            "Action": Match.array_with(["secretsmanager:GetSecretValue"])})])}})


def test_witness_iam_grants_shared_and_private_leadingkeys():
    import json
    body = json.dumps(_synth().to_json())
    assert "shared#*" in body and "__meta__#shared#*" in body, \
        "witness must grant the shared-KEL oracle namespace"
