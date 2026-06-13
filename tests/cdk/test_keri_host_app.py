"""Tests for the keri_host ecosystem app (witness + mailbox, no core table)."""
import aws_cdk as cdk
from aws_cdk.assertions import Template
from keri_cdk import WitnessStack, MailboxStack


def test_keri_host_is_witness_plus_mailbox_no_core():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    w = WitnessStack(app, "W",
                     name="witness",
                     alias="witness",
                     domain_name="w.ex.com",
                     hosted_zone_id="Z123ABC456DEF7",
                     witness_url="https://w.ex.com",
                     env=env)
    m = MailboxStack(app, "M",
                     name="mailbox",
                     alias="mailbox",
                     domain_name="m.ex.com",
                     hosted_zone_id="Z123ABC456DEF7",
                     mailbox_url="https://m.ex.com",
                     env=env)
    tw = Template.from_stack(w)
    tm = Template.from_stack(m)

    # Each stack has exactly one DynamoDB table (own Baser only — no core table)
    tw.resource_count_is("AWS::DynamoDB::Table", 1)
    tm.resource_count_is("AWS::DynamoDB::Table", 1)

    # Neither stack has a 'keri-core' table (no KeriCoreStack in the app)
    for t in (tw, tm):
        tables = t.find_resources("AWS::DynamoDB::Table")
        assert all(
            p["Properties"].get("TableName") != "keri-core"
            for p in tables.values()
        ), "Found unexpected keri-core table in keri_host stack"


def test_witness_baser_table_name():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    w = WitnessStack(app, "Wt",
                     name="witness",
                     alias="witness",
                     domain_name="w.ex.com",
                     hosted_zone_id="Z123ABC456DEF7",
                     witness_url="https://w.ex.com",
                     env=env)
    tw = Template.from_stack(w)
    tables = tw.find_resources("AWS::DynamoDB::Table")
    names = [p["Properties"].get("TableName") for p in tables.values()]
    assert "witness-db" in names, f"Expected 'witness-db' in {names}"


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
