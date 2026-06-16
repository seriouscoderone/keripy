import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import MailboxStack, KeriCoreStack

ENV = cdk.Environment(account="111111111111", region="us-east-1")


def _synth():
    app = cdk.App()
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=ENV)
    s = MailboxStack(app, "Mbx", name="mailbox-test", alias="mailbox",
                     domain_name="mailbox.example.com", hosted_zone_id="Z123ABC456DEF7",
                     mailbox_url="https://mailbox.example.com",
                     core_table=core.table, env=ENV)
    return Template.from_stack(s)


def test_mailbox_lwa_streaming_env_arm64_no_reserved_concurrency():
    t = _synth()
    fns = t.find_resources("AWS::Lambda::Function")
    props = list(fns.values())[0]["Properties"]
    env = props["Environment"]["Variables"]
    assert env["AWS_LWA_INVOKE_MODE"] == "response_stream"
    assert env["LD_LIBRARY_PATH"] == "/opt/lib"
    assert "ReservedConcurrentExecutions" not in props
    assert props["Architectures"] == ["arm64"]
    assert props["Runtime"] == "python3.14"
    assert len(props["Layers"]) >= 2     # KeriRuntimeLayer + LWA


def test_mailbox_api_is_regional():
    t = _synth()
    t.has_resource_properties("AWS::ApiGateway::RestApi", {
        "EndpointConfiguration": {"Types": ["REGIONAL"]}})


def test_mailbox_method_has_response_streaming():
    """Every API GW method integration must carry the streaming mode + 15-min timeout.

    The streaming integration is the most fragile property of the mailbox: a
    CDK version bump or an accidental switch from RestApi to LambdaRestApi can
    silently drop ResponseTransferMode/TimeoutInMillis, breaking long-poll
    clients.  Both root (ANY /) and proxy (ANY /{proxy+}) methods are checked.
    """
    t = _synth()
    methods = t.find_resources("AWS::ApiGateway::Method")
    integrations = [m["Properties"].get("Integration", {}) for m in methods.values()]
    assert any(i.get("ResponseTransferMode") == "STREAM" for i in integrations), (
        "no AWS::ApiGateway::Method Integration has ResponseTransferMode=STREAM; "
        "the mailbox streaming integration is missing or misconfigured"
    )
    assert any(i.get("TimeoutInMillis") == 900000 for i in integrations), (
        "no AWS::ApiGateway::Method Integration has TimeoutInMillis=900000 (15 min); "
        "the mailbox streaming timeout is missing or misconfigured"
    )


def test_mailbox_owns_no_table():
    t = _synth()
    t.resource_count_is("AWS::DynamoDB::Table", 0)


def test_mailbox_leadingkeys_scoped_iam():
    t = _synth()
    t.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {"Statement":
        Match.array_with([Match.object_like({"Condition": Match.object_like({
            "ForAllValues:StringLike": Match.object_like({
                "dynamodb:LeadingKeys": Match.any_value()})})})])}})


def test_mailbox_namespace_env_present():
    t = _synth()
    t.has_resource_properties("AWS::Lambda::Function", {
        "Environment": {"Variables": Match.object_like({"MAILBOX_NAMESPACE": Match.any_value()})}})


def test_mailbox_imports_core_table_lock():
    import json
    body = json.dumps(_synth().to_json())
    assert "Fn::ImportValue" in body, "mailbox must import the core table (cross-stack lock)"


def test_mailbox_iam_grants_shared_and_private_leadingkeys():
    import json
    body = json.dumps(_synth().to_json())
    assert "shared#*" in body and "__meta__#shared#*" in body, \
        "mailbox must grant the shared-KEL oracle namespace"
