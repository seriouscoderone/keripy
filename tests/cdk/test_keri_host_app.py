"""Tests for the keri_host ecosystem app (witness uses core table; mailbox owns its own)."""
import aws_cdk as cdk
from aws_cdk.assertions import Template
from keri_cdk import WitnessStack, MailboxStack, KeriCoreStack


def test_keri_host_witness_uses_core_table_no_own_table():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=env)
    w = WitnessStack(app, "W", name="witness", alias="witness",
                     domain_name="w.ex.com", hosted_zone_id="Z123ABC456DEF7",
                     witness_url="https://w.ex.com", core_table=core.table, env=env)
    tw = Template.from_stack(w)
    tw.resource_count_is("AWS::DynamoDB::Table", 0)
    tc = Template.from_stack(core)
    tc.has_resource_properties("AWS::DynamoDB::Table", {"TableName": "keri-core"})


def test_mailbox_baser_table_name():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    m = MailboxStack(app, "Mb",
                     name="mailbox",
                     alias="mailbox",
                     domain_name="m.ex.com",
                     hosted_zone_id="Z123ABC456DEF7",
                     mailbox_url="https://m.ex.com",
                     env=env)
    tm = Template.from_stack(m)
    tables = tm.find_resources("AWS::DynamoDB::Table")
    names = [p["Properties"].get("TableName") for p in tables.values()]
    assert "mailbox-db" in names, f"Expected 'mailbox-db' in {names}"
