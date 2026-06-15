"""MailboxStack: deploys a KERI Mailbox as a zip + TWO-layer Lambda (the
KeriRuntimeLayer + the AWS Lambda Web Adapter layer) running the Falcon ASGI
app under uvicorn, with native API-Gateway response streaming.

Translates ``sam-mailbox/template.yaml`` into CDK constructs:
  - Shared DynamoDB core table (passed in as ``core_table``; LeadingKeys-scoped to
                                the ``{STACK_NAME}:mbx`` namespace)
  - KeriRuntimeLayer           (arm64 libsodium + keripy native deps)
  - AWS Lambda Web Adapter      (arm64 layer; provides /opt/bootstrap exec-wrapper
                                + the localhost->Lambda streaming sidecar)
  - Lambda Function            (python3.14, arm64, run.sh -> uvicorn, 15-min timeout,
                                NO reserved concurrency — streaming/long-poll fan-out)
  - Scoped Secrets Manager IAM  (Get/Create/Put on keri/<stack>/*)
  - REST API (RestApi)         REGIONAL, response-streaming LambdaIntegration
                                (ResponseTransferMode.STREAM) on ANY / and ANY /{proxy+}
  - ACM Certificate            (DNS validation, synth-safe via from_hosted_zone_attributes)
  - API GW custom domain       (REGIONAL)
  - Route53 A-record alias

LWA wiring (the documented LWA zip exec-wrapper pattern):
  - ``handler="run.sh"`` — Lambda runs the exec-wrapper, which exec's run.sh.
  - ``AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap`` — the LWA layer ships /opt/bootstrap;
    Lambda runs it instead of the managed runtime, and it launches run.sh
    (uvicorn). LWA then proxies inbound Lambda invocations to localhost:8080.
  - ``AWS_LWA_INVOKE_MODE=response_stream`` — LWA uses the streaming Lambda
    Runtime API endpoint (pairs with API GW ResponseTransferMode.STREAM).
  - ``AWS_LWA_PORT=8080`` / ``AWS_LWA_READINESS_CHECK_PATH=/status``.
"""
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

# Secrets Manager actions required by the mailbox handler (get-or-create the
# keeper secret on cold start, then read/write it). Source:
# sam-mailbox/template.yaml Policies.Statement.Action — identical to witness.
_SM_ACTIONS = [
    "secretsmanager:GetSecretValue",
    "secretsmanager:CreateSecret",
    "secretsmanager:PutSecretValue",
]

_HANDLER_DIR = "keri_cdk/handlers/mailbox"

# AWS Lambda Web Adapter is published by AWS account 753240598075. The arm64
# layer name is ``LambdaAdapterLayerArm64``. The version MUST be a CURRENT one
# for the deploy region — verify before the real deploy (Task 9). :25 is a
# recent release used as the synth-level default.
_LWA_DEFAULT_VERSION = 25


def _default_lwa_arn() -> str:
    return (
        f"arn:aws:lambda:{Aws.REGION}:753240598075:"
        f"layer:LambdaAdapterLayerArm64:{_LWA_DEFAULT_VERSION}"
    )


class MailboxStack(Stack):
    """CDK translation of sam-mailbox/template.yaml: zip + KeriRuntimeLayer +
    AWS LWA layer, uvicorn-served Falcon ASGI app, native REGIONAL response
    streaming."""

    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        name: str,
        alias: str,
        domain_name: str,
        hosted_zone_id: str,
        mailbox_url: str,
        core_table: "ddb.ITable",
        witness_aid: str = "",
        witness_url: str = "",
        keeper_secret: str | None = None,
        runtime_layer: KeriRuntimeLayer | None = None,
        lwa_layer_arn: str | None = None,
        memory: int = 1024,
        **kw,
    ):
        super().__init__(scope, cid, **kw)

        # --- Layers: KeriRuntimeLayer + AWS Lambda Web Adapter ------------------
        runtime_construct = runtime_layer or KeriRuntimeLayer(self, "Runtime")
        keri_layer = runtime_construct.layer
        self.lwa_layer = _lambda.LayerVersion.from_layer_version_arn(
            self, "LWA", lwa_layer_arn or _default_lwa_arn()
        )

        # --- Keeper secret path -------------------------------------------------
        resolved_keeper_secret = keeper_secret or f"keri/{Aws.STACK_NAME}/keeper"

        # --- Lambda function (zip + 2 layers + LWA exec-wrapper) ----------------
        # python3.14 + arm64 (keripy pins python_requires>=3.14.0).
        # handler="run.sh": LWA's /opt/bootstrap exec-wrapper exec's it (uvicorn).
        # 15-min timeout: matches the SSE long-poll / streaming hard cap.
        # NO reserved_concurrent_executions: the mailbox fans out many concurrent
        # long-poll/streaming clients (unlike the single-writer witness).
        # LD_LIBRARY_PATH=/opt/lib: libsodium is at /opt/lib in KeriRuntimeLayer.
        self.fn = _lambda.Function(
            self,
            "MailboxFunction",
            # Name from the (account-unique) stack name, not `name`, so a temp/parallel
            # deploy never collides with another stack's function (e.g. the live SAM
            # `mailbox-handler`). Stack names are guaranteed unique per account/region.
            function_name=f"{Aws.STACK_NAME}-handler",
            runtime=_lambda.Runtime.PYTHON_3_14,
            architecture=_lambda.Architecture.ARM_64,
            handler="run.sh",
            code=_lambda.Code.from_asset(_HANDLER_DIR),
            layers=[keri_layer, self.lwa_layer],
            timeout=Duration.minutes(15),
            memory_size=memory,
            environment={
                # ---- LWA streaming knobs (sam-mailbox/template.yaml) ----------
                "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                "AWS_LWA_INVOKE_MODE": "response_stream",
                "AWS_LWA_PORT": "8080",
                "AWS_LWA_READINESS_CHECK_PATH": "/status",
                # ---- Mailbox app config ----------------------------------------
                "MAILBOX_NAME": name,
                "MAILBOX_ALIAS": alias,
                "MAILBOX_BASER_TABLE": core_table.table_name,
                "MAILBOX_NAMESPACE": f"{Aws.STACK_NAME}:mbx",  # consumed by the handler in Task 3
                "MAILBOX_KEEPER_SECRET": resolved_keeper_secret,
                "MAILBOX_REGION": Aws.REGION,
                "MAILBOX_ENDPOINT_URL": "",
                "MAILBOX_URL": mailbox_url,
                "WITNESS_AID": witness_aid,
                "WITNESS_URL": witness_url,
                # libsodium ships in KeriRuntimeLayer at /opt/lib.
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
                            f"{Aws.STACK_NAME}:*#*",
                            f"__meta__#{Aws.STACK_NAME}:*",
                        ]
                    }
                },
            )
        )

        # --- IAM: Secrets Manager (scoped to keri/<stack>/*) -------------------
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
        hosted_zone = r53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=hosted_zone_id,
            zone_name=".".join(domain_name.split(".")[-2:]),
        )
        self.cert = acm.Certificate(
            self,
            "MailboxCert",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        # --- REST API (REGIONAL, response streaming) ----------------------------
        # Explicit RestApi (proxy wired by hand) so we control the streaming
        # integration. REGIONAL endpoint + binary_media_types mirror the SAM
        # MailboxApi. The mailbox serves ANY / and ANY /{proxy+} (the Falcon app
        # routes internally: /, /oobi/*, /status, /fwd, qry r=/mbx, ...).
        self.api = apigw.RestApi(
            self,
            "MailboxApi",
            rest_api_name=f"{name}-api",
            endpoint_types=[apigw.EndpointType.REGIONAL],
            binary_media_types=["application/cesr", "*/*"],
            deploy_options=apigw.StageOptions(stage_name="Prod"),
        )

        # Response-streaming integration. ResponseTransferMode.STREAM is native
        # in aws-cdk-lib 2.259 — CDK rewrites the integration URI to
        # /response-streaming-invocations automatically (no CfnMethod escape
        # hatch). timeout=15min matches the streaming hard cap.
        streaming_integration = apigw.LambdaIntegration(
            self.fn,
            response_transfer_mode=apigw.ResponseTransferMode.STREAM,
            timeout=Duration.minutes(15),
        )

        # ANY /  and  ANY /{proxy+}  (matches MailboxRootMethod / MailboxProxyMethod)
        self.api.root.add_method("ANY", streaming_integration)
        proxy = self.api.root.add_resource("{proxy+}")
        proxy.add_method("ANY", streaming_integration)

        # --- API GW Custom Domain (REGIONAL) ------------------------------------
        self.domain = apigw.DomainName(
            self,
            "MailboxDomain",
            domain_name=domain_name,
            certificate=self.cert,
            endpoint_type=apigw.EndpointType.REGIONAL,
            mapping=self.api,
        )

        # --- Route53 A-record alias --------------------------------------------
        r53.ARecord(
            self,
            "MailboxDnsRecord",
            zone=hosted_zone,
            record_name=domain_name,
            target=r53.RecordTarget.from_alias(
                targets.ApiGatewayDomain(self.domain)
            ),
        )

        # --- Outputs ------------------------------------------------------------
        CfnOutput(self, "MailboxUrl", value=mailbox_url)
        CfnOutput(self, "MailboxApiGw", value=self.api.url)
        CfnOutput(self, "MailboxNamespace", value=f"{Aws.STACK_NAME}:mbx")
        CfnOutput(self, "MailboxKeeperSecret", value=resolved_keeper_secret)
