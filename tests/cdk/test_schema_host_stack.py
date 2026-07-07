import aws_cdk as cdk
from aws_cdk import aws_apigateway as apigw
from aws_cdk.assertions import Template

from keri_cdk.schema_host_stack import SchemaHostStack


def _stack():
    app = cdk.App()
    host = cdk.Stack(app, "Host", env=cdk.Environment(account="111111111111", region="us-east-1"))
    api = apigw.RestApi(host, "WriteApi")
    api.root.add_method("ANY")
    stack = SchemaHostStack(app, "SchemaHost", domain_name="schema.keri.host",
                            hosted_zone_id="Z123", write_api=api,
                            env=cdk.Environment(account="111111111111", region="us-east-1"))
    return Template.from_stack(stack)


def test_creates_cas_bucket_and_distribution():
    t = _stack()
    t.resource_count_is("AWS::S3::Bucket", 1)
    t.resource_count_is("AWS::CloudFront::Distribution", 1)


def test_distribution_has_oobi_and_write_behaviors():
    t = _stack()
    t.has_resource_properties("AWS::CloudFront::Distribution", {
        "DistributionConfig": {
            "Aliases": ["schema.keri.host"],
        }
    })
