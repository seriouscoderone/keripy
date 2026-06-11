"""Shared KeriCoreStack: one pooled DynamoDB table for all services' public state."""
from aws_cdk import Stack, RemovalPolicy, CfnOutput
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ssm as ssm
from constructs import Construct

CORE_TABLE_SSM = "/serviceaid/core-table-name"
GSI_NAME = "subdb-index"


class KeriCoreStack(Stack):
    """Pooled Tier-1 KERI-state table (KEL/Baser + TEL/Reger), namespaced per service."""

    def __init__(self, scope: Construct, cid: str, *, table_name: str = "keri-core", **kw):
        super().__init__(scope, cid, **kw)

        self.table = ddb.Table(
            self, "CoreTable",
            table_name=table_name,
            partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            # WARNING: RETAIN + fixed table_name is intentional — this table must
            # outlive any stack lifecycle. After `cdk destroy`, the orphaned table
            # blocks redeploy (ResourceAlreadyExists): re-import it before redeploying.
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.table.add_global_secondary_index(
            index_name=GSI_NAME,
            partition_key=ddb.Attribute(name="gsi_pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="gsi_sk", type=ddb.AttributeType.STRING),
        )

        # TODO(before production): enable point-in-time recovery and
        # deletion_protection — this pooled table holds EVERY service's KEL/TEL.

        ssm.StringParameter(self, "CoreTableNameParam",
                            parameter_name=CORE_TABLE_SSM,
                            string_value=self.table.table_name)
        CfnOutput(self, "CoreTableName", value=self.table.table_name)
