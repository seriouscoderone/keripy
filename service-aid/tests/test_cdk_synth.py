import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk import App
from aws_cdk.assertions import Template
from serviceaid.cdk.keri_core_stack import KeriCoreStack


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
