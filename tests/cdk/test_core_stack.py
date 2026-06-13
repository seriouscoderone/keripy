import aws_cdk as cdk
from aws_cdk.assertions import Template
from keri_cdk import KeriCoreStack

def _synth():
    app = cdk.App()
    stack = KeriCoreStack(app, "Core", table_name="keri-core")
    return Template.from_stack(stack), stack

def test_core_table_pitr_and_deletion_protection():
    t, _ = _synth()
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "TableName": "keri-core",
        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        "DeletionProtectionEnabled": True,
    })
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "GlobalSecondaryIndexes": [{"IndexName": "subdb-index"}],
    })

def test_core_table_retained_and_stack_termination_protected():
    t, stack = _synth()
    t.has_resource("AWS::DynamoDB::Table", {"DeletionPolicy": "Retain"})
    assert stack.termination_protection is True

def test_core_table_exposed_as_attribute():
    _, stack = _synth()
    assert stack.table is not None
