"""CDK app: shared core stack + the allowlist-gated Gated Retrieval Service-AID.

Deploys via ServiceAidFunction: the dev's compute_code (this dir) + the two
framework layers. Passing core.table across the stack boundary emits the
cross-stack Export/Fn::ImportValue lifecycle lock. The stub `lookup` table shows
the IGrantable pattern: my_lookup.grant_read_data(svc) adds a read policy to the
service Function's role the canonical CDK way.

Build BOTH layers before `cdk deploy` (see DEPLOY_RUNBOOK.md):
  keri_cdk/layers/build_layer.sh
  keri_cdk/layers/build_framework_layer.sh
Pass --context allowlist='["EReqAID", ...]' to gate by sender AID (empty ⇒ any)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import aws_cdk as cdk
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_dynamodb as ddb

from keri_cdk import KeriCoreStack, ServiceAidFunction
from keri_cdk.service_aid import inject_handler_shim

app = cdk.App()
env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1")

core = KeriCoreStack(app, "KeriCore", table_name="keri-core", env=env)

svc_stack = cdk.Stack(app, "GatedRetrieval", env=env)

# compute_code = this example dir (gated_handler.py + schema/). Inject the shim
# (robust handler resolution) before Code.from_asset stages it.
_asset_dir = str(pathlib.Path(__file__).parent)
inject_handler_shim(_asset_dir)

svc = ServiceAidFunction(
    svc_stack, "Gated",
    alias="gated",
    core_table=core.table,
    compute_code=_lambda.Code.from_asset(_asset_dir),
    handler_ref="gated_handler:svc",
    witnesses=app.node.try_get_context("witnesses") or [],
    toad=int(app.node.try_get_context("toad") or 0),
)

# IGrantable pattern: a stub lookup resource the service may read.
lookup = ddb.Table(svc_stack, "GatedLookup",
                   partition_key=ddb.Attribute(name="recordId",
                                               type=ddb.AttributeType.STRING))
lookup.grant_read_data(svc)   # adds a read policy to the service Function's role

svc_stack.add_dependency(core)
app.synth()
