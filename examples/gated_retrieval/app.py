"""CDK app: shared core stack + the allowlist-gated Gated Retrieval Service AID.

The core table is OWNED by KeriCore; passing core.table into ServiceAid emits the
cross-stack Export/Fn::ImportValue lock so GatedRetrieval cannot be torn down
while it consumes the pooled table (and the table outlives the service stack).

Allowlist: pass `--context allowlist='["EReqAID", ...]'` to gate by sender AID;
an empty allowlist means any verified sender is accepted.
"""
import pathlib
import sys

# keri_cdk lives at the repo root and is not pip-installed, so put the repo root
# (examples/gated_retrieval -> examples -> repo root) on sys.path before
# importing it. `cdk synth` runs this from the example dir, so cwd is not enough.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import aws_cdk as cdk

from keri_cdk import KeriCoreStack, ServiceAid

app = cdk.App()
env = cdk.Environment(region=app.node.try_get_context("region") or "us-east-1")
core = KeriCoreStack(app, "KeriCore", table_name="keri-core", env=env)
svc = cdk.Stack(app, "GatedRetrieval", env=env)
ServiceAid(svc, "Gated", alias="gated", core_table=core.table,
           handler_module="gated_handler",
           allowlist=app.node.try_get_context("allowlist") or [])
svc.add_dependency(core)
app.synth()
