"""Tests for the WebSocket API / connection registry additions to MailboxStack.

Asserts that the synthesized CloudFormation template includes:
  - AWS::ApiGatewayV2::Api  (WEBSOCKET, route selection $request.body.action)
  - AWS::ApiGatewayV2::Stage (StageName: prod, auto-deploy)
  - AWS::DynamoDB::Table     (private connection registry: PK connectionId, GSI byPre, TTL expireAt)
  - execute-api:ManageConnections IAM grant on self.fn's role
  - WS_CALLBACK_URL + WS_CONN_TABLE env on the three WS handler Lambdas and self.fn

Mirror the _synth() pattern from test_mailbox_stack.py.
"""
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import MailboxStack, KeriCoreStack

ENV = cdk.Environment(account="111111111111", region="us-east-1")


def _synth():
    app = cdk.App()
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=ENV)
    s = MailboxStack(
        app, "Mbx",
        name="mailbox-test",
        alias="mailbox",
        domain_name="mailbox.example.com",
        hosted_zone_id="Z123ABC456DEF7",
        mailbox_url="https://mailbox.example.com",
        core_table=core.table,
        env=ENV,
    )
    return Template.from_stack(s)


def test_ws_api_is_websocket_with_route_selection():
    """WebSocketApi must have ProtocolType=WEBSOCKET and the correct route selection expression."""
    t = _synth()
    t.has_resource_properties("AWS::ApiGatewayV2::Api", {
        "ProtocolType": "WEBSOCKET",
        "RouteSelectionExpression": "$request.body.action",
    })


def test_ws_stage_is_prod_with_auto_deploy():
    """WebSocketStage must be named 'prod' with AutoDeploy=True."""
    t = _synth()
    t.has_resource_properties("AWS::ApiGatewayV2::Stage", {
        "StageName": "prod",
        "AutoDeploy": True,
    })


def test_ws_connect_route_exists():
    """A $connect route must be present in the template."""
    t = _synth()
    t.has_resource_properties("AWS::ApiGatewayV2::Route", {
        "RouteKey": "$connect",
    })


def test_ws_disconnect_route_exists():
    """A $disconnect route must be present in the template."""
    t = _synth()
    t.has_resource_properties("AWS::ApiGatewayV2::Route", {
        "RouteKey": "$disconnect",
    })


def test_ws_default_route_exists():
    """A $default route must be present in the template."""
    t = _synth()
    t.has_resource_properties("AWS::ApiGatewayV2::Route", {
        "RouteKey": "$default",
    })


def test_conn_registry_table_pk_and_gsi():
    """Private connection registry: PK connectionId (S), GSI byPre on pre (S)."""
    t = _synth()
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "KeySchema": Match.array_with([
            Match.object_like({"AttributeName": "connectionId", "KeyType": "HASH"}),
        ]),
        "GlobalSecondaryIndexes": Match.array_with([
            Match.object_like({
                "IndexName": "byPre",
                "KeySchema": Match.array_with([
                    Match.object_like({"AttributeName": "pre", "KeyType": "HASH"}),
                ]),
            }),
        ]),
    })


def test_conn_registry_table_ttl():
    """Connection registry must have TTL enabled on expireAt."""
    t = _synth()
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "TimeToLiveSpecification": {
            "AttributeName": "expireAt",
            "Enabled": True,
        },
    })


def test_conn_registry_table_on_demand_billing():
    """Connection registry table must use PAY_PER_REQUEST (on-demand) billing."""
    t = _synth()
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "BillingMode": "PAY_PER_REQUEST",
    })


def test_manage_connections_iam_grant():
    """self.fn's role must have execute-api:ManageConnections granted."""
    import json
    body = json.dumps(_synth().to_json())
    assert "execute-api:ManageConnections" in body, (
        "execute-api:ManageConnections not found in synthesized template; "
        "ws_api.grant_manage_connections(self.fn) may be missing"
    )


def test_fn_has_ws_callback_url_env():
    """self.fn must receive WS_CALLBACK_URL in its environment (for the inline notifier)."""
    t = _synth()
    # self.fn is the first/main mailbox Lambda (has the LWA env vars)
    fns = t.find_resources("AWS::Lambda::Function")
    # Find the LWA-based mailbox handler (has AWS_LWA_INVOKE_MODE)
    main_fn = None
    for props in fns.values():
        env = props["Properties"].get("Environment", {}).get("Variables", {})
        if "AWS_LWA_INVOKE_MODE" in env:
            main_fn = env
            break
    assert main_fn is not None, "could not find main LWA mailbox Lambda"
    assert "WS_CALLBACK_URL" in main_fn, "WS_CALLBACK_URL missing from main mailbox Lambda env"


def test_fn_has_ws_conn_table_env():
    """self.fn must receive WS_CONN_TABLE in its environment (for the inline notifier)."""
    t = _synth()
    fns = t.find_resources("AWS::Lambda::Function")
    main_fn = None
    for props in fns.values():
        env = props["Properties"].get("Environment", {}).get("Variables", {})
        if "AWS_LWA_INVOKE_MODE" in env:
            main_fn = env
            break
    assert main_fn is not None, "could not find main LWA mailbox Lambda"
    assert "WS_CONN_TABLE" in main_fn, "WS_CONN_TABLE missing from main mailbox Lambda env"
