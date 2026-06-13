import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk import App
from aws_cdk.assertions import Template
from keri_cdk import KeriCoreStack


def test_core_stack_creates_pooled_table_and_ssm_export():
    app = App()
    stack = KeriCoreStack(app, "KeriCore", table_name="keri-core")
    t = Template.from_stack(stack)
    t.resource_count_is("AWS::DynamoDB::Table", 1)
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "TableName": "keri-core",
        "BillingMode": "PAY_PER_REQUEST",
    })
    t.resource_count_is("AWS::SSM::Parameter", 1)

    t.has_resource_properties("AWS::DynamoDB::Table", {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [{
            "IndexName": "subdb-index",
            "KeySchema": [
                {"AttributeName": "gsi_pk", "KeyType": "HASH"},
                {"AttributeName": "gsi_sk", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
    })

    t.has_resource("AWS::DynamoDB::Table", {
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
    })


def test_service_aid_construct_provisions_lambda_apigw_keeper(tmp_path):
    from aws_cdk import App, Stack
    from aws_cdk.assertions import Template
    from serviceaid.cdk.service_aid_construct import ServiceAid

    # Provide a stub Dockerfile so DockerImageCode.from_image_asset can
    # fingerprint the directory at synth time without a real Docker build.
    (tmp_path / "Dockerfile").write_text("FROM public.ecr.aws/lambda/python:3.12\n")

    app = App()
    stack = Stack(app, "RatingSvc")
    ServiceAid(stack, "Rating",
               alias="rating", core_table_name="keri-core",
               handler_module="rating_handler",
               witnesses=["BWit1", "BWit2"], toad=2,
               allowlist=["Ealice"],
               image_directory=str(tmp_path), dockerfile="Dockerfile")
    t = Template.from_stack(stack)
    # cr.Provider synthesizes its own framework Lambda(s), so don't count
    # functions — assert the service function exists by name/env instead.
    #
    # The construct owns NO DynamoDB tables (the core table is referenced by
    # name via from_table_name) and creates NO SecretsManager secret — the
    # keeper secret keri/<alias>/keeper is get-or-created at deploy time by the
    # inception Custom Resource, not declared in CloudFormation.
    t.resource_count_is("AWS::DynamoDB::Table", 0)
    t.resource_count_is("AWS::SecretsManager::Secret", 0)
    t.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "rating-serviceaid",
        "Environment": {"Variables": {"SERVICEAID_ALIAS": "rating"}}
    })
    # The keeper secret NAME must be wired into the runtime via env so the
    # runtime + the inception CR address the same secret.
    t.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "rating-serviceaid",
        "Environment": {"Variables": {"SERVICEAID_KEEPER_SECRET": "keri/rating/keeper"}}
    })
    # Authz config must be reachable through the construct: allowlist is
    # comma-joined into SERVICEAID_ALLOWLIST so the working allowlist policy
    # can actually be enabled by a deployer.
    t.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "rating-serviceaid",
        "Environment": {"Variables": {"SERVICEAID_ALLOWLIST": "Ealice"}}
    })

    # The scoped core-table policy must grant DescribeTable: DynamoDBer.open ->
    # _ensure_table calls describe_table unconditionally on the core table, and
    # only ResourceNotFound is swallowed — AccessDenied would be re-raised on
    # every cold start. Find the scoped statement (the one bearing the
    # LeadingKeys condition) and confirm DescribeTable is among its actions.
    found = False
    for pol in t.find_resources("AWS::IAM::Policy").values():
        for stmt in pol["Properties"]["PolicyDocument"]["Statement"]:
            actions = stmt.get("Action")
            if (isinstance(actions, list)
                    and "dynamodb:DescribeTable" in actions
                    and "Condition" in stmt
                    and "ForAllValues:StringLike" in stmt["Condition"]):
                found = True
    assert found, "scoped core-table policy must include dynamodb:DescribeTable"

    # The function role must grant scoped Secrets Manager read on the keeper
    # secret namespace keri/rating/*. The fn doubles as the inception CR
    # handler, so GetSecretValue (runtime read) must be present and the
    # resource must match the per-alias namespace.
    secret_found = False
    for pol in t.find_resources("AWS::IAM::Policy").values():
        for stmt in pol["Properties"]["PolicyDocument"]["Statement"]:
            actions = stmt.get("Action")
            acts = actions if isinstance(actions, list) else [actions]
            resource = stmt.get("Resource")
            resources = resource if isinstance(resource, list) else [resource]
            if ("secretsmanager:GetSecretValue" in acts
                    and any(isinstance(r, str)
                            and r.endswith("secret:keri/rating/*")
                            for r in resources)):
                secret_found = True
    assert secret_found, ("function role must grant secretsmanager:GetSecretValue "
                          "on keri/rating/*")


def test_service_aid_construct_rejects_unsafe_alias(tmp_path):
    import pytest
    from aws_cdk import App, Stack
    from serviceaid.cdk.service_aid_construct import ServiceAid

    (tmp_path / "Dockerfile").write_text("FROM public.ecr.aws/lambda/python:3.12\n")
    app = App()
    stack = Stack(app, "BadSvc")
    # A '*' in the alias would silently widen the IAM LeadingKeys patterns and
    # collapse the multi-tenant boundary — must be rejected at synth time.
    with pytest.raises(ValueError, match="LeadingKeys"):
        ServiceAid(stack, "Bad",
                   alias="bad*alias", core_table_name="keri-core",
                   handler_module="bad_handler",
                   image_directory=str(tmp_path))
