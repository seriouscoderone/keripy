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
    """self.fn's role policy must include execute-api:ManageConnections.

    Anchored to an actual IAM::Policy resource, not a raw JSON substring.
    CDK renders a single-action grant as a scalar string (not an array), so
    we iterate policies and check each statement's Action directly.
    """
    t = _synth()
    policies = t.find_resources("AWS::IAM::Policy")
    found = False
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"].get("Statement", []):
            action = stmt.get("Action", [])
            # CDK may render a single action as a string instead of a list.
            if action == "execute-api:ManageConnections" or (
                isinstance(action, list) and "execute-api:ManageConnections" in action
            ):
                found = True
                break
        if found:
            break
    assert found, (
        "execute-api:ManageConnections not found in any AWS::IAM::Policy; "
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


def test_ws_default_fn_has_ws_conn_table_env():
    """ws_default_fn (subscribe handler) must carry WS_CONN_TABLE in its environment."""
    t = _synth()
    fns = t.find_resources("AWS::Lambda::Function")
    default_fn_env = None
    for props in fns.values():
        p = props["Properties"]
        if p.get("Handler") == "ws_handlers.default":
            default_fn_env = p.get("Environment", {}).get("Variables", {})
            break
    assert default_fn_env is not None, "could not find ws_handlers.default Lambda"
    assert "WS_CONN_TABLE" in default_fn_env, "WS_CONN_TABLE missing from ws_default_fn env"


def test_disconnect_fn_iam_is_delete_only():
    """$disconnect handler must have only DeleteItem on the registry table — no PutItem.

    §5.6: disconnect = DeleteItem ONLY.  A broad grant_write_data would silently
    include PutItem/UpdateItem/BatchWriteItem; this assertion catches regressions.
    CDK may render a single-action statement as a scalar string, so we normalise
    both forms before comparing.
    """
    t = _synth()
    policies = t.find_resources("AWS::IAM::Policy")
    delete_only_found = False
    for policy in policies.values():
        stmts = policy["Properties"]["PolicyDocument"].get("Statement", [])
        for stmt in stmts:
            actions = stmt.get("Action", [])
            # Normalise scalar → list.
            if isinstance(actions, str):
                actions = [actions]
            if actions == ["dynamodb:DeleteItem"]:
                delete_only_found = True
                # Also confirm PutItem is absent (catches partial-overlap grants).
                assert "dynamodb:PutItem" not in actions, (
                    "$disconnect policy must not include PutItem"
                )
                break
        if delete_only_found:
            break
    assert delete_only_found, (
        "$disconnect policy with exactly ['dynamodb:DeleteItem'] not found; "
        "disconnect may be over-permissioned (grant_write_data includes PutItem)"
    )


def test_default_fn_registry_policy_includes_gsi_arn():
    """ws_default_fn's registry policy must include Query and a /index/* resource.

    §5.6: subscribe = PutItem + Query(GSI).  CDK's grant_read_write_data omits
    the GSI ARN, so a future broad-grant regression would pass synth but fail at
    runtime on byPre queries.  This assertion catches that regression.
    """
    t = _synth()
    policies = t.find_resources("AWS::IAM::Policy")
    gsi_query_found = False
    for policy in policies.values():
        stmts = policy["Properties"]["PolicyDocument"].get("Statement", [])
        for stmt in stmts:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if "dynamodb:Query" not in actions:
                continue
            # Check that at least one resource ends in /index/*
            resources = stmt.get("Resource", [])
            if not isinstance(resources, list):
                resources = [resources]
            for res in resources:
                # Resources rendered as Fn::Join dicts in synth; check string
                # representation for the /index/* suffix marker.
                res_str = str(res)
                if "/index/*" in res_str:
                    gsi_query_found = True
                    break
            if gsi_query_found:
                break
        if gsi_query_found:
            break
    assert gsi_query_found, (
        "No IAM policy found with dynamodb:Query + a /index/* resource; "
        "ws_default_fn may be missing the GSI ARN in its registry policy"
    )


def test_main_fn_registry_policy_includes_query_and_gsi_arn():
    """§5.5: self.fn (deposit handler) must have dynamodb:Query with the GSI
    ARN so the inline notifier can look up live connectionIds via byPre.

    CDK grant helpers omit the index ARN; the explicit PolicyStatement in
    Change B is the only reliable way to verify this is present.
    """
    t = _synth()
    # Collect all IAM policies that are attached to the main LWA Lambda's role.
    # Strategy: find all policies, look for one with both dynamodb:Query and
    # dynamodb:DeleteItem (the two actions granted by Change B), where at least
    # one resource ends in /index/*.
    policies = t.find_resources("AWS::IAM::Policy")
    found = False
    for policy in policies.values():
        stmts = policy["Properties"]["PolicyDocument"].get("Statement", [])
        for stmt in stmts:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if "dynamodb:Query" not in actions or "dynamodb:DeleteItem" not in actions:
                continue
            # Both actions present — check for /index/* resource (GSI ARN).
            resources = stmt.get("Resource", [])
            if not isinstance(resources, list):
                resources = [resources]
            for res in resources:
                if "/index/*" in str(res):
                    found = True
                    break
            if found:
                break
        if found:
            break
    assert found, (
        "self.fn policy with dynamodb:Query + dynamodb:DeleteItem + /index/* resource "
        "not found; Change B (self.fn registry IAM) may be missing"
    )


# ---------------------------------------------------------------------------
# Task 5 (Change B): MAILBOX_WS_URL env on self.fn (distinct from WS_CALLBACK_URL)
# ---------------------------------------------------------------------------

def test_fn_has_mailbox_ws_url_env():
    """self.fn must receive MAILBOX_WS_URL in its environment (the wss:// client-connect URL).

    §5.7: MAILBOX_WS_URL = ws_stage.url (wss://…/prod), the endpoint clients dial.
    This is distinct from WS_CALLBACK_URL = ws_stage.callback_url (the https://…/prod
    management endpoint used by the inline notifier to push nudges).
    """
    t = _synth()
    fns = t.find_resources("AWS::Lambda::Function")
    main_fn_env = None
    for props in fns.values():
        env = props["Properties"].get("Environment", {}).get("Variables", {})
        if "AWS_LWA_INVOKE_MODE" in env:
            main_fn_env = env
            break
    assert main_fn_env is not None, "could not find main LWA mailbox Lambda"
    assert "MAILBOX_WS_URL" in main_fn_env, (
        "MAILBOX_WS_URL missing from main mailbox Lambda env; "
        "add_environment('MAILBOX_WS_URL', ws_stage.url) may be absent"
    )


def test_mailbox_ws_url_is_distinct_from_ws_callback_url():
    """MAILBOX_WS_URL and WS_CALLBACK_URL must both be present and have different values.

    MAILBOX_WS_URL = ws_stage.url (wss://…/prod) — the connect endpoint clients dial.
    WS_CALLBACK_URL = ws_stage.callback_url (https://…/prod) — the mgmt endpoint for nudges.
    They must not be accidentally conflated (both should be non-empty and differ).
    """
    t = _synth()
    fns = t.find_resources("AWS::Lambda::Function")
    main_fn_env = None
    for props in fns.values():
        env = props["Properties"].get("Environment", {}).get("Variables", {})
        if "AWS_LWA_INVOKE_MODE" in env:
            main_fn_env = env
            break
    assert main_fn_env is not None, "could not find main LWA mailbox Lambda"
    ws_url = main_fn_env.get("MAILBOX_WS_URL")
    cb_url = main_fn_env.get("WS_CALLBACK_URL")
    assert ws_url is not None, "MAILBOX_WS_URL must be present"
    assert cb_url is not None, "WS_CALLBACK_URL must be present"
    assert ws_url != cb_url, (
        f"MAILBOX_WS_URL ({ws_url!r}) must differ from WS_CALLBACK_URL ({cb_url!r}); "
        "they are two different API GW endpoints (connect vs manage-connections)"
    )
