"""ServiceAid construct: per-service Lambda + API Gateway + scoped IAM + keeper.

Handler naming convention: the developer handler file baked into the image
(e.g. /var/task/rating_handler.py) must have a basename equal to the
``handler_module`` prop — runtime.init() imports it by module name via
SERVICEAID_HANDLER. Concretely: if ``handler_module="rating_handler"`` then
the Dockerfile must ADD / COPY a file named ``rating_handler.py`` to
``/var/task/``. See service-aid/Dockerfile for the canonical example.
"""
from __future__ import annotations

import re

from aws_cdk import Duration, CustomResource
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_iam as iam
from aws_cdk import custom_resources as cr
from constructs import Construct


class ServiceAid(Construct):
    """One Service AID: container Lambda over the shared core table (own namespace)
    + an isolated, encrypted keeper table + bran secret + inception Custom Resource.

    The pooled core table is referenced by name only (``core_table_name``).
    IAM access is scoped to this service's namespace prefixes via
    ``dynamodb:LeadingKeys``:

    * ``{alias}:*#*``        – KEL + TEL rows
    * ``__meta__#{alias}:*`` – keripy meta rows

    The keeper table is a dedicated, encrypted DynamoDB table that holds the
    private key material for this service's AID only (never pooled).

    Inception happens via a CloudFormation Custom Resource that reuses the
    service Lambda as its ``on_event_handler``.  ``handler.py`` routes events
    carrying a ``RequestType`` key to ``inception.on_event``, so no separate
    CR Lambda is needed.  ``cr.Provider`` does synthesise its own framework
    Lambda, however — test assertions should match functions by ``FunctionName``
    rather than counting all Lambda resources.

    Docker image: ``image_directory`` is the Docker BUILD CONTEXT and MUST be
    the repo root, because the bundled Dockerfile's ``COPY src/keri`` and
    ``COPY service-aid/...`` paths resolve relative to the context root.
    ``dockerfile`` is the Dockerfile path relative to that context (it is NOT at
    the context root — it lives under ``service-aid/``), defaulting to
    ``service-aid/Dockerfile``.
    """

    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        alias: str,
        core_table_name: str,
        handler_module: str,
        witnesses: list[str] | None = None,
        toad: int = 0,
        image_directory: str = ".",
        dockerfile: str = "service-aid/Dockerfile",
        memory: int = 1024,
        timeout_seconds: int = 120,
        **kw,
    ):
        super().__init__(scope, cid, **kw)
        if not re.fullmatch(r"[a-z0-9-]+", alias):
            raise ValueError(f"alias must match [a-z0-9-]+ (got {alias!r}) — "
                             "it is interpolated into IAM LeadingKeys patterns")
        witnesses = witnesses or []

        # ── Tier-1 reference: shared core table (not owned by this stack) ──────
        core_table = ddb.Table.from_table_name(self, "CoreTable", core_table_name)

        # ── Tier-2: isolated, encrypted keeper table (never pooled) ─────────────
        keeper_table = ddb.Table(
            self,
            "KeeperTable",
            table_name=f"{alias}-ks",
            partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=ddb.TableEncryption.AWS_MANAGED,
        )
        keeper_table.add_global_secondary_index(
            index_name="subdb-index",
            partition_key=ddb.Attribute(
                name="gsi_pk", type=ddb.AttributeType.STRING
            ),
            sort_key=ddb.Attribute(
                name="gsi_sk", type=ddb.AttributeType.STRING
            ),
        )

        # ── Keeper passcode (bran) — generated once, never logged ───────────────
        bran = sm.Secret(
            self,
            "KeeperBran",
            secret_name=f"{alias}/bran",
            generate_secret_string=sm.SecretStringGenerator(
                password_length=32, exclude_punctuation=True
            ),
        )

        env = {
            "SERVICEAID_ALIAS": alias,
            "SERVICEAID_CORE_TABLE": core_table_name,
            "SERVICEAID_KEEPER_TABLE": keeper_table.table_name,
            "SERVICEAID_WITNESSES": ",".join(witnesses),
            "SERVICEAID_TOAD": str(toad),
            "SERVICEAID_HANDLER": handler_module,
            "SERVICEAID_BRAN_SECRET": bran.secret_name,
            # config._dynamo_kwa passes region=cfg.region explicitly to boto3,
            # and cfg.region defaults to "us-east-1" when this env var is absent
            # — which would override Lambda's real AWS_REGION. Set it explicitly.
            "SERVICEAID_REGION": self.node.try_get_context("region") or "us-east-1",
            # LD_LIBRARY_PATH needed for keripy's libsodium binding inside Lambda.
            "LD_LIBRARY_PATH": "/var/task/lib",
        }

        # ── Service Lambda (container image) ────────────────────────────────────
        fn = _lambda.DockerImageFunction(
            self,
            "Function",
            function_name=f"{alias}-serviceaid",
            code=_lambda.DockerImageCode.from_image_asset(image_directory, file=dockerfile),
            memory_size=memory,
            timeout=Duration.seconds(timeout_seconds),
            architecture=_lambda.Architecture.ARM_64,
            environment=env,
        )

        # ── IAM ──────────────────────────────────────────────────────────────────
        # Keeper table: full CRUD (only this service's AID lives here).
        keeper_table.grant_read_write_data(fn)
        # Bran secret: read-only.
        bran.grant_read(fn)
        # Core (pooled) table: scoped to this service's namespace prefixes only.
        #
        # SECURITY-CRITICAL + UNVERIFIED: the multi-tenant boundary for the
        # pooled core table rests on dynamodb:LeadingKeys scoping GSI queries
        # (subdb-index) by the namespaced gsi_pk. AWS's handling of LeadingKeys
        # for *index* queries is not validated here (moto does not enforce IAM
        # conditions). MUST be empirically verified before production: deploy two
        # aliases and confirm a cross-tenant GSI Query from one role is DENIED.
        # If LeadingKeys is not populated for index queries, this condition is
        # vacuous and the pooled-table design needs rework (per-tenant tables or
        # payload encryption). See plan Task 12 review.
        #
        # NOTE: DescribeTable has no item keys, so it is vacuously allowed under
        # the LeadingKeys condition. It is required because DynamoDBer.open ->
        # _ensure_table calls describe_table unconditionally on the core table
        # (baser + reger); without it every cold start hits AccessDenied.
        fn.add_to_role_policy(
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
                            f"{alias}:*#*",
                            f"__meta__#{alias}:*",
                        ]
                    }
                },
            )
        )

        # ── API Gateway: proxy all routes to the Lambda ──────────────────────────
        # binary_media_types enables CESR (application/cesr) passthrough.
        api = apigw.LambdaRestApi(
            self,
            "Api",
            handler=fn,
            proxy=True,
            binary_media_types=["application/cesr", "*/*"],
        )

        self.api = api
        self.function = fn

        # ── Inception Custom Resource ─────────────────────────────────────────────
        # The service Lambda doubles as the CR on_event handler — handler.py
        # routes events that carry a "RequestType" key to inception.on_event.
        # cr.Provider synthesises its own framework Lambda internally; test
        # assertions must NOT count all Lambdas but match by FunctionName.
        provider = cr.Provider(self, "InceptionProvider", on_event_handler=fn)
        self.inception = CustomResource(
            self,
            "Inception",
            service_token=provider.service_token,
            properties={"Alias": alias},
        )
