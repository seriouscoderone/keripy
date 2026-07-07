import pathlib

import aws_cdk as cdk
from aws_cdk import aws_lambda as _lambda
from aws_cdk.assertions import Template

from keri_cdk.core_stack import KeriCoreStack
from keri_cdk.schema_host_stack import SchemaHostStack
from keri_cdk.service_aid import inject_handler_shim

_SCHEMA_HOST_DIR = str(
    pathlib.Path(__file__).resolve().parents[2] / "examples" / "schema_host")


def _stack():
    """Construct the single-stack SchemaHostStack (owns the publish
    Service-AID + the dedicated CAS bucket + CloudFront)."""
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    core = KeriCoreStack(app, "KeriCore", table_name="keri-core", env=env)
    inject_handler_shim(_SCHEMA_HOST_DIR)
    stack = SchemaHostStack(
        app, "SchemaHost",
        alias="schema-publisher",
        core_table=core.table,
        compute_code=_lambda.Code.from_asset(_SCHEMA_HOST_DIR),
        handler_ref="schema_host_handler:svc",
        domain_name="schema.keri.host",
        hosted_zone_id="Z123",
        env=env,
    )
    return Template.from_stack(stack)


def test_creates_cas_bucket_and_distribution():
    t = _stack()
    t.resource_count_is("AWS::S3::Bucket", 1)
    t.resource_count_is("AWS::CloudFront::Distribution", 1)


def test_publisher_lambda_present():
    """The publish Service-AID's Lambda Function lives in THIS stack (write
    plane), so read-source and write-target are the same bucket."""
    t = _stack()
    fns = t.find_resources("AWS::Lambda::Function")
    assert len(fns) >= 1, f"expected >=1 Lambda Function, got {len(fns)}"


def test_distribution_alias_configured():
    t = _stack()
    t.has_resource_properties("AWS::CloudFront::Distribution", {
        "DistributionConfig": {
            "Aliases": ["schema.keri.host"],
        }
    })
