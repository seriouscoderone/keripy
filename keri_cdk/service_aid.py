"""ServiceAidFunction construct: one Service-AID = a python3.14/arm64 zip Lambda
(the dev's compute_code) riding TWO layers — KeriRuntimeLayer (libsodium + keripy)
and ServiceAidFrameworkLayer (keri_serviceaid) — over the shared pooled core table.

The handler resolves from the framework layer (keri_serviceaid.handler.handler);
the dev's compute_code module (handler_ref module:attr, e.g. "gated_handler:svc")
ships in the asset. iam.IGrantable lets adopters grant their own resources to the
Function the canonical CDK way: my_lookup.grant_read_data(svc).

Inherited unchanged from Phase B/C:
  - cross-stack core-table lifecycle LOCK (core_table: ITable across a stack
    boundary -> Export/Fn::ImportValue);
  - four-pattern dynamodb:LeadingKeys union (shared#*, __meta__#shared#*,
    {alias}:*#*, __meta__#{alias}:*);
  - keeper-secret IAM scoped to keri/<alias>/*;
  - inception Custom Resource (the Function doubles as on_event);
  - API Gateway CESR ingest (binary_media_types, proxy, 204).

Real-deploy UNKNOWN (validated in Task 11): a layer-resident handler
(keri_serviceaid.handler.handler at /opt/python) importing the dev's /var/task
compute_code which imports the framework layer, with libsodium from
KeriRuntimeLayer (/opt/lib). FALLBACK if Lambda will not resolve a layer-resident
handler: a 3-line shim handler.py is auto-injected into the asset
(inject_handler_shim) so the deploy is robust either way; the handler string
stays "keri_serviceaid.handler.handler" and the shim is a redundant safety net."""
from __future__ import annotations

import os
import re

import jsii
from aws_cdk import Aws, Duration, CustomResource
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import custom_resources as cr
from constructs import Construct

try:
    from .runtime_layer import KeriRuntimeLayer
    from .framework_layer import ServiceAidFrameworkLayer
except ImportError:  # pragma: no cover
    from keri_cdk.runtime_layer import KeriRuntimeLayer
    from keri_cdk.framework_layer import ServiceAidFrameworkLayer


@jsii.implements(iam.IGrantable)
class ServiceAidFunction(Construct):
    """One Service-AID Function: compute_code zip + two layers over the shared
    core table (own namespace) + keeper secret + inception Custom Resource."""

    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        alias: str,
        core_table: ddb.ITable,
        compute_code: _lambda.Code,
        handler_ref: str = "service:svc",
        witnesses: list[str] | None = None,
        toad: int = 0,
        runtime_layer: KeriRuntimeLayer | None = None,
        framework_layer: ServiceAidFrameworkLayer | None = None,
        environment: dict | None = None,
        memory: int = 1024,
        timeout_seconds: int = 120,
        vpc=None,
        extra_layers: list | None = None,
        **kw,
    ):
        super().__init__(scope, cid, **kw)
        if not re.fullmatch(r"[a-z0-9-]+", alias):
            raise ValueError(f"alias must match [a-z0-9-]+ (got {alias!r}) — it is "
                             "interpolated into IAM LeadingKeys patterns")
        witnesses = witnesses or []

        klayer = (runtime_layer or KeriRuntimeLayer(self, "Runtime")).layer
        flayer = (framework_layer or ServiceAidFrameworkLayer(self, "Framework")).layer

        framework_env = {
            "SERVICEAID_ALIAS": alias,
            "SERVICEAID_CORE_TABLE": core_table.table_name,
            "SERVICEAID_KEEPER_SECRET": f"keri/{alias}/keeper",
            "SERVICEAID_WITNESSES": ",".join(witnesses),
            "SERVICEAID_TOAD": str(toad),
            "SERVICEAID_HANDLER": handler_ref,     # module:attr
            "SERVICEAID_REGION": Aws.REGION,
            "LD_LIBRARY_PATH": "/opt/lib",
        }
        env = {**framework_env, **(environment or {})}

        fn = _lambda.Function(
            self, "Function",
            function_name=f"{alias}-serviceaid",
            runtime=_lambda.Runtime.PYTHON_3_14,
            architecture=_lambda.Architecture.ARM_64,
            handler="keri_serviceaid.handler.handler",   # resolves from framework layer
            code=compute_code,
            layers=[klayer, flayer, *(extra_layers or [])],
            reserved_concurrent_executions=1,
            memory_size=memory,
            timeout=Duration.seconds(timeout_seconds),
            environment=env,
            vpc=vpc,
        )

        # Keeper secret scoped to keri/<alias>/* (fn doubles as the inception CR).
        keeper_secret_arn = f"arn:aws:secretsmanager:*:*:secret:keri/{alias}/*"
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue",
                     "secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"],
            resources=[keeper_secret_arn]))

        # Pooled core table scoped to the four-pattern LeadingKeys union.
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:DescribeTable", "dynamodb:GetItem", "dynamodb:PutItem",
                     "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:BatchWriteItem"],
            resources=[core_table.table_arn, f"{core_table.table_arn}/index/*"],
            conditions={"ForAllValues:StringLike": {"dynamodb:LeadingKeys": [
                "shared#*", "__meta__#shared#*",
                f"{alias}:*#*", f"__meta__#{alias}:*"]}}))

        # API Gateway: CESR ingest proxy, returns 204.
        api = apigw.LambdaRestApi(self, "Api", handler=fn, proxy=True,
                                  binary_media_types=["application/cesr", "*/*"])

        self.api = api
        self.function = fn

        # Inception Custom Resource (fn doubles as on_event_handler).
        provider = cr.Provider(self, "InceptionProvider", on_event_handler=fn)
        self.inception = CustomResource(self, "Inception",
                                        service_token=provider.service_token,
                                        properties={"Alias": alias})

    @property
    def grant_principal(self):       # iam.IGrantable
        """Delegate to the Function's role so adopters can grant their own
        resources the canonical way: my_lookup_table.grant_read_data(svc)."""
        return self.function.grant_principal


def inject_handler_shim(asset_dir: str) -> None:
    """Auto-inject the 3-line handler.py shim into a compute_code asset dir so the
    deploy is robust whether or not Lambda resolves the layer-resident handler.
    Callers (the example app) run this on the staged asset before Code.from_asset.
    Harmless when the layer-resident handler resolves."""
    shim = os.path.join(asset_dir, "handler.py")
    if not os.path.exists(shim):
        with open(shim, "w") as f:
            f.write("from keri_serviceaid.handler import handler  # noqa: F401\n")
