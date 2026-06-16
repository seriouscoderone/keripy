"""WitnessStack: deploys a KERI Witness as a zip+KeriRuntimeLayer Lambda.

Translates ``sam-witness/template.yaml`` into CDK constructs:
  - Shared DynamoDB core table (passed in as ``core_table``; LeadingKeys-scoped namespace)
  - KeriRuntimeLayer      (arm64 libsodium + keripy native deps)
  - Lambda Function       (python3.14, arm64, reserved_concurrent_executions=1)
  - Scoped Secrets Manager IAM policy
  - REST API (LambdaRestApi) with explicit routes + REGIONAL endpoint
  - ACM Certificate       (DNS validation, synth-safe via from_hosted_zone_attributes)
  - API GW custom domain  (REGIONAL)
  - Route53 A-record alias
"""
import os

from aws_cdk import (
    Stack,
    Duration,
    Aws,
    CfnOutput,
    aws_dynamodb as ddb,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_apigateway as apigw,
    aws_certificatemanager as acm,
    aws_route53 as r53,
    aws_route53_targets as targets,
)
from constructs import Construct

from .runtime_layer import KeriRuntimeLayer

# Secrets Manager actions required by the witness handler (get-or-create the
# keeper secret on cold start, then read/write it on every invocation).
# Source: sam-witness/template.yaml Policies.Statement.Action
_SM_ACTIONS = [
    "secretsmanager:GetSecretValue",
    "secretsmanager:CreateSecret",
    "secretsmanager:PutSecretValue",
]

# Absolute (dirname-based) so Code.from_asset resolves regardless of the cdk app's
# CWD — e.g. `cdk deploy` from ecosystems/keri_host/. Mirrors service_aid.py.
_HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handlers", "witness")


class WitnessStack(Stack):
    """CDK translation of sam-witness/template.yaml using zip + KeriRuntimeLayer."""

    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        name: str,
        alias: str,
        domain_name: str,
        hosted_zone_id: str,
        witness_url: str,
        core_table: "ddb.ITable",
        keeper_secret: str | None = None,
        witnesses: list | None = None,  # unused at synth time; reserved for app config
        toad: int = 0,  # unused at synth time; reserved for app config
        runtime_layer: KeriRuntimeLayer | None = None,
        memory: int = 1024,
        timeout_seconds: int = 120,
        **kw,
    ):
        super().__init__(scope, cid, **kw)

        # --- Layer --------------------------------------------------------------
        layer_construct = runtime_layer or KeriRuntimeLayer(self, "Runtime")
        layer = layer_construct.layer

        # --- Keeper secret path -------------------------------------------------
        # SAM template used !Sub "keri/${AWS::StackName}/keeper"
        resolved_keeper_secret = keeper_secret or f"keri/{Aws.STACK_NAME}/keeper"

        # --- Lambda function ----------------------------------------------------
        # python3.14 + arm64: keripy pins python_requires>=3.14.0
        # LD_LIBRARY_PATH=/opt/lib: libsodium is at /opt/lib in the KeriRuntimeLayer
        # reserved_concurrent_executions=1: single-writer guarantee per witness
        self.fn = _lambda.Function(
            self,
            "WitnessFunction",
            # Name from the (account-unique) stack name, not `name`, so a temp/parallel
            # deploy never collides with another stack's function (e.g. the live SAM
            # `witness-handler`). Stack names are guaranteed unique per account/region.
            function_name=f"{Aws.STACK_NAME}-handler",
            runtime=_lambda.Runtime.PYTHON_3_14,
            architecture=_lambda.Architecture.ARM_64,
            handler="witness_handler.handler",
            code=_lambda.Code.from_asset(_HANDLER_DIR),
            layers=[layer],
            reserved_concurrent_executions=1,
            timeout=Duration.seconds(timeout_seconds),
            memory_size=memory,
            environment={
                "WITNESS_NAME": name,
                "WITNESS_ALIAS": alias,
                "WITNESS_BASER_TABLE": core_table.table_name,
                "WITNESS_NAMESPACE": f"{Aws.STACK_NAME}:kel",  # consumed by the handler in Task 3
                "WITNESS_KEEPER_SECRET": resolved_keeper_secret,
                "WITNESS_REGION": Aws.REGION,
                "WITNESS_URL": witness_url,
                "WITNESS_ENDPOINT_URL": "",
                "LD_LIBRARY_PATH": "/opt/lib",
            },
        )

        # --- IAM: Core (pooled) table — LeadingKeys-scoped to this stack's namespace only ---
        self.fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:DescribeTable",
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:BatchWriteItem",
                ],
                resources=[
                    core_table.table_arn,
                    f"{core_table.table_arn}/index/*",
                ],
                conditions={
                    "ForAllValues:StringLike": {
                        "dynamodb:LeadingKeys": [
                            "shared#*",
                            "__meta__#shared#*",
                            f"{Aws.STACK_NAME}:*#*",
                            f"__meta__#{Aws.STACK_NAME}:*",
                        ]
                    }
                },
            )
        )

        # --- IAM: Secrets Manager (scoped to keri/<stack>/*) -------------------
        # Matches sam-witness/template.yaml Policies.Statement with
        # Resource: !Sub "arn:${AWS::Partition}:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:keri/${AWS::StackName}/*"
        self.fn.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=_SM_ACTIONS,
                resources=[
                    f"arn:{Aws.PARTITION}:secretsmanager:{Aws.REGION}:{Aws.ACCOUNT_ID}:secret:keri/{Aws.STACK_NAME}/*"
                ],
            )
        )

        # --- ACM Certificate (DNS-validated, synth-safe) -----------------------
        # Using from_hosted_zone_attributes so synthesis requires no AWS context
        # lookup calls (avoids the HostedZone.from_lookup latency + credential need).
        hosted_zone = r53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=hosted_zone_id,
            # zone_name must be derivable from domain_name; strip the apex label
            zone_name=".".join(domain_name.split(".")[-2:]),
        )
        self.cert = acm.Certificate(
            self,
            "WitnessCert",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        # --- REST API -----------------------------------------------------------
        # LambdaRestApi with proxy=False so we can declare explicit routes
        # matching the SAM event entries exactly.
        # binary_media_types mirrors the SAM Globals.Api.BinaryMediaTypes section.
        # REGIONAL endpoint matches WitnessApiDomainName in the SAM template.
        self.api = apigw.LambdaRestApi(
            self,
            "WitnessApi",
            handler=self.fn,
            proxy=False,
            endpoint_types=[apigw.EndpointType.REGIONAL],
            binary_media_types=["application/cesr", "*/*"],
            rest_api_name=f"{name}-api",
        )

        # Route definitions matching sam-witness/template.yaml Events section
        fn_integration = apigw.LambdaIntegration(self.fn)
        root = self.api.root

        # POST /   PUT /   GET /
        root.add_method("POST", fn_integration)
        root.add_method("PUT", fn_integration)
        root.add_method("GET", fn_integration)

        # POST /receipts   GET /receipts
        receipts = root.add_resource("receipts")
        receipts.add_method("POST", fn_integration)
        receipts.add_method("GET", fn_integration)

        # GET /query
        query = root.add_resource("query")
        query.add_method("GET", fn_integration)

        # GET /oobi   GET /oobi/{aid}   GET /oobi/{aid}/{role}   GET /oobi/{aid}/{role}/{eid}
        oobi = root.add_resource("oobi")
        oobi.add_method("GET", fn_integration)
        oobi_aid = oobi.add_resource("{aid}")
        oobi_aid.add_method("GET", fn_integration)
        oobi_role = oobi_aid.add_resource("{role}")
        oobi_role.add_method("GET", fn_integration)
        oobi_eid = oobi_role.add_resource("{eid}")
        oobi_eid.add_method("GET", fn_integration)

        # --- API GW Custom Domain (REGIONAL) ------------------------------------
        self.domain = apigw.DomainName(
            self,
            "WitnessDomain",
            domain_name=domain_name,
            certificate=self.cert,
            endpoint_type=apigw.EndpointType.REGIONAL,
            mapping=self.api,
        )

        # --- Route53 A-record alias --------------------------------------------
        r53.ARecord(
            self,
            "WitnessDnsRecord",
            zone=hosted_zone,
            record_name=domain_name,
            target=r53.RecordTarget.from_alias(
                targets.ApiGatewayDomain(self.domain)
            ),
        )

        # --- Outputs ------------------------------------------------------------
        CfnOutput(self, "WitnessUrl", value=f"https://{domain_name}")
        CfnOutput(
            self,
            "WitnessApiGw",
            value=self.api.url,
        )
        CfnOutput(self, "WitnessNamespace", value=f"{Aws.STACK_NAME}:kel")
        CfnOutput(
            self,
            "WitnessKeeperSecret",
            value=resolved_keeper_secret,
        )
