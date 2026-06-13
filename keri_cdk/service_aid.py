"""ServiceAid construct: per-service zip+layer Lambda + API Gateway + scoped IAM
+ keeper + inception Custom Resource, over the shared (pooled) KERI core table.

Deployment shape (Phase B): a python3.14 / arm64 **zip** Lambda riding the shared
``KeriRuntimeLayer`` (libsodium + keripy native deps at /opt). No Docker at
deploy time — mirrors WitnessStack / MailboxStack.

Cross-stack core-table lock (the lifecycle LOCK)
------------------------------------------------
``core_table`` is an ``ITable`` passed in from a DIFFERENT stack (typically
``KeriCoreStack(...).table``). Referencing ``core_table.table_arn`` /
``core_table.table_name`` across a stack boundary makes CDK automatically emit a
CloudFormation ``Export`` on the owning stack and an ``Fn::ImportValue`` here —
so this service stack can never be deleted while the core table's export is
consumed, and the pooled table outlives any single service. (Both stacks need a
concrete ``env`` account/region for the cross-stack reference to resolve to an
``Fn::ImportValue`` rather than a token.)

Developer business compute (``handler_module``)
-----------------------------------------------
``runtime.init()`` does ``importlib.import_module(handler_module)``, so the
developer's handler file must sit on the Lambda's import path (the asset dir,
extracted to /var/task). ``handler_code_path`` is the asset directory; it
defaults to the serviceaid runtime dir (``keri_cdk/handlers/serviceaid``), which
carries ``handler.py`` (the entrypoint), ``bootstrap.py`` (libsodium shim),
``_inception``-import shim, and the runtime modules.

SYNTH-LEVEL / Task 9 bundling note: at synth time we only assert the Lambda's
shape. The consuming app's ``handler_module`` file (e.g. ``gated_handler.py``)
plus a ``serviceaid`` import shim and ``_inception.py`` must be *co-located in
the asset directory* for a real deploy. The clean Task 9 approach is to point
``handler_code_path`` at a single staging dir that contains the serviceaid
runtime files + the developer handler (built by a small bundling step), or to
add the developer file to the serviceaid asset via CDK BundlingOptions. The
default keeps synth single-sourced; the example (examples/gated_retrieval) ships
``gated_handler.py`` + schemas alongside and documents this seam.

Keeper
------
The keeper lives in ONE KMS-encrypted Secrets Manager secret per service,
``keri/<alias>/keeper`` (JSON ``{v, salt, bran, keeper-blob}``). This construct
does NOT create that secret in CloudFormation — the inception Custom Resource
get-or-creates it at deploy time (race-safe, create-only) so the salt/bran exist
before the first incept and survive stack churn. The construct only sets
``SERVICEAID_KEEPER_SECRET`` (the secret name) and grants scoped Secrets Manager
access on ``keri/<alias>/*``.

Multi-tenant core-table scoping
-------------------------------
IAM access to the pooled core table is scoped to this service's namespace
prefixes via ``dynamodb:LeadingKeys``:

* ``{alias}:*#*``        – KEL + TEL rows
* ``__meta__#{alias}:*`` – keripy meta rows

Inception
---------
A CloudFormation Custom Resource reuses the service Lambda as its
``on_event_handler``. ``handler.py`` routes events carrying a ``RequestType``
key to ``keri_cdk._inception.on_event``, so no separate CR Lambda is needed.
``cr.Provider`` does synthesise its own framework Lambda — test assertions
should match functions by ``FunctionName`` rather than counting all Lambdas.

Authorization
-------------
``allowlist`` is the set of sender AIDs permitted to invoke this service
(comma-joined into ``SERVICEAID_ALLOWLIST``; empty ⇒ any verified sender).
``required_schema`` is DEFERRED in v1 — the handler does not yet extract
caller-presented ACDCs, so a non-empty value would deny all requests; leave it
unset. Use ``allowlist`` for v1 sender gating.
"""
from __future__ import annotations

import os
import re

from aws_cdk import Aws, Duration, CustomResource
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import custom_resources as cr
from constructs import Construct

try:
    from .runtime_layer import KeriRuntimeLayer
except ImportError:  # pragma: no cover - direct-module import fallback
    from keri_cdk.runtime_layer import KeriRuntimeLayer

# The serviceaid Lambda asset (handler.py + bootstrap.py + runtime modules).
# Default code path; the developer's handler_module file is bundled alongside
# for a real deploy (see module docstring / Task 9).
_HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handlers", "serviceaid")


class ServiceAid(Construct):
    """One Service AID: zip+layer Lambda over the shared core table (own
    namespace) + a keeper secret + inception Custom Resource."""

    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        alias: str,
        core_table: ddb.ITable,
        handler_module: str,
        witnesses: list[str] | None = None,
        toad: int = 0,
        allowlist: list[str] | None = None,
        required_schema: str = "",
        handler_code_path: str = _HANDLER_DIR,
        runtime_layer: KeriRuntimeLayer | None = None,
        memory: int = 1024,
        timeout_seconds: int = 120,
        **kw,
    ):
        super().__init__(scope, cid, **kw)
        if not re.fullmatch(r"[a-z0-9-]+", alias):
            raise ValueError(f"alias must match [a-z0-9-]+ (got {alias!r}) — "
                             "it is interpolated into IAM LeadingKeys patterns")
        witnesses = witnesses or []

        # ── Cross-stack core table (the lifecycle LOCK) ────────────────────────
        # core_table is passed in from another stack; referencing its arn/name
        # across the boundary makes CDK emit the Export/Fn::ImportValue lock.

        # ── Layer ──────────────────────────────────────────────────────────────
        layer = (runtime_layer or KeriRuntimeLayer(self, "Runtime")).layer

        env = {
            "SERVICEAID_ALIAS": alias,
            "SERVICEAID_CORE_TABLE": core_table.table_name,
            "SERVICEAID_KEEPER_SECRET": f"keri/{alias}/keeper",
            "SERVICEAID_WITNESSES": ",".join(witnesses),
            "SERVICEAID_TOAD": str(toad),
            "SERVICEAID_ALLOWLIST": ",".join(allowlist or []),
            "SERVICEAID_REQUIRED_SCHEMA": required_schema,
            "SERVICEAID_HANDLER": handler_module,
            # config._dynamo_kwa passes region=cfg.region explicitly to boto3,
            # and cfg.region defaults to "us-east-1" when this env var is absent
            # — which would override Lambda's real AWS_REGION. Set it to the real
            # region token so boto3 targets the deploy region.
            "SERVICEAID_REGION": Aws.REGION,
            # libsodium ships in the KeriRuntimeLayer at /opt/lib (zip+layer
            # shape), reachable via LD_LIBRARY_PATH. bootstrap.ensure_libsodium()
            # also patches find_library to this path.
            "LD_LIBRARY_PATH": "/opt/lib",
        }

        # ── Service Lambda (zip + KeriRuntimeLayer) ──────────────────────────────
        # python3.14 + arm64: keripy pins python_requires>=3.14.0.
        # reserved_concurrent_executions=1: single-writer guarantee per service AID
        # (mirrors WitnessStack — the AID's KEL/keeper must not race across
        # concurrent cold starts).
        fn = _lambda.Function(
            self,
            "Function",
            function_name=f"{alias}-serviceaid",
            runtime=_lambda.Runtime.PYTHON_3_14,
            architecture=_lambda.Architecture.ARM_64,
            handler="handler.handler",
            code=_lambda.Code.from_asset(handler_code_path),
            layers=[layer],
            reserved_concurrent_executions=1,
            memory_size=memory,
            timeout=Duration.seconds(timeout_seconds),
            environment=env,
        )

        # ── IAM ──────────────────────────────────────────────────────────────────
        # Keeper secret: scoped to this service's keri/<alias>/* namespace. The
        # fn doubles as the inception CR handler, so it needs both read
        # (steady-state runtime) and create/put (CR get-or-create).
        keeper_secret_arn = f"arn:aws:secretsmanager:*:*:secret:keri/{alias}/*"
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue",
                     "secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"],
            resources=[keeper_secret_arn]))
        # NOTE: CreateSecret/PutSecretValue are needed only by the inception CR;
        # a dedicated CR role would let the steady-state fn be GetSecretValue-only.
        # Core (pooled) table: scoped to this service's namespace prefixes only.
        #
        # SECURITY-CRITICAL (VERIFIED): the multi-tenant boundary for the pooled
        # core table rests on dynamodb:LeadingKeys scoping GSI queries
        # (subdb-index) by the namespaced gsi_pk.  This was empirically verified
        # against real AWS — the probe at keri_cdk/probes/leadingkeys/probe.py
        # (see its README) created two tenant roles with the exact production
        # policy, seeded both namespaces, then confirmed from tenant A's role:
        #   - cross-tenant GSI Query (tenant B's gsi_pk)   → DENIED  ← the crux
        #   - shared __meta__ GSI Query (another tenant)   → DENIED
        #   - own GSI Query and base-table ops              → ALLOW (as expected)
        # AWS does populate dynamodb:LeadingKeys for index queries; the boundary
        # is sound.  moto/DynamoDB-Local do not enforce IAM conditions, so the
        # probe must be re-run after any IAM policy change or major keripy key
        # schema change (i.e. whenever gsi_pk shape changes).
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
        # routes events that carry a "RequestType" key to _inception.on_event.
        # cr.Provider synthesises its own framework Lambda internally; test
        # assertions must NOT count all Lambdas but match by FunctionName.
        provider = cr.Provider(self, "InceptionProvider", on_event_handler=fn)
        self.inception = CustomResource(
            self,
            "Inception",
            service_token=provider.service_token,
            properties={"Alias": alias},
        )
