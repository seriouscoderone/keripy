"""Tests for keri_cdk.federation.build_federation (the 1x1 -> 5x5 loop)."""
import aws_cdk as cdk
from aws_cdk import assertions

from keri_cdk.federation import build_federation

ENTRIES = [{"slug": s, "domain": f"{s.lower()}.test", "hosted_zone_id": f"Z{i}"}
           for i, s in enumerate(["Alpha", "Bravo", "Charlie", "Delta", "Echo"])]


def _app():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    return app, env


def test_builds_core_plus_pair_per_entry():
    app, env = _app()
    built = build_federation(app, ENTRIES, env)
    assert len(built["witnesses"]) == 5
    assert len(built["mailboxes"]) == 5
    stacks = [c for c in app.node.children if isinstance(c, cdk.Stack)]
    assert len(stacks) == 11  # 1 core + 5 witness + 5 mailbox


def test_stack_ids_are_domain_derived_not_indexed():
    app, env = _app()
    built = build_federation(app, ENTRIES, env)
    assert built["witnesses"]["Alpha"].stack_name == "WitnessAlpha"
    assert built["mailboxes"]["Echo"].stack_name == "MailboxEcho"
    # No index-based names leaked in.
    names = {s.stack_name for s in app.node.children if isinstance(s, cdk.Stack)}
    assert "Witness0" not in names and "Mailbox0" not in names


def test_witness_uses_expected_subdomain_and_url():
    # WitnessStack does not store domain_name/witness_url as instance attributes;
    # assert via CloudFormation template inspection instead.
    app, env = _app()
    built = build_federation(app, ENTRIES, env)
    w = built["witnesses"]["Bravo"]
    template = assertions.Template.from_stack(w)
    # DomainName property on the API GW custom domain
    template.has_resource_properties(
        "AWS::ApiGateway::DomainName",
        {"DomainName": "witness.bravo.test"},
    )
    # WITNESS_URL env var on the Lambda function must carry the https:// scheme
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": assertions.Match.object_like(
                {
                    "Variables": assertions.Match.object_like(
                        {"WITNESS_URL": "https://witness.bravo.test"}
                    )
                }
            )
        },
    )


def test_mailbox_uses_expected_subdomain_and_url():
    # Symmetric to test_witness_uses_expected_subdomain_and_url.
    app, env = _app()
    built = build_federation(app, ENTRIES, env)
    m = built["mailboxes"]["Bravo"]
    template = assertions.Template.from_stack(m)
    # DomainName property on the API GW custom domain
    template.has_resource_properties(
        "AWS::ApiGateway::DomainName",
        {"DomainName": "mailbox.bravo.test"},
    )
    # MAILBOX_URL env var on the Lambda function must carry the https:// scheme
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": assertions.Match.object_like(
                {
                    "Variables": assertions.Match.object_like(
                        {"MAILBOX_URL": "https://mailbox.bravo.test"}
                    )
                }
            )
        },
    )
