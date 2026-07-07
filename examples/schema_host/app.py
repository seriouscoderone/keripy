"""CDK app for schema.keri.host: pooled core table + the single SchemaHostStack
that OWNS the publish Service-AID (write plane) + the dedicated S3 CAS +
CloudFront (read plane).

SINGLE STACK (not two): the read-source and write-target are the SAME bucket —
CloudFront serves exactly the objects the publish Lambda writes. Splitting into a
service stack (holds the Lambda) and a host stack (holds the bucket + takes the
service's write_api) would make each stack reference the other → a CIRCULAR STACK
DEPENDENCY and ``cdk synth`` fails. SchemaHostStack owns both, so the
bucket<->Lambda wiring is intra-stack and acyclic.

Deploy (all context values come from a gitignored deploy config in reality — no
personal domains/AIDs committed):
  cdk deploy KeriCore SchemaHost
    --context account=<acct> --context region=us-east-1
    --context domain=schema.keri.host --context hosted_zone_id=<zid>
    --context allowlist='["EPub1",...]'
    --context witnesses='["Bwit1",...]' --context toad=2
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import aws_cdk as cdk
from aws_cdk import aws_lambda as _lambda

from keri_cdk import KeriCoreStack, SchemaHostStack
from keri_cdk.service_aid import inject_handler_shim

app = cdk.App()
env = cdk.Environment(
    account=app.node.try_get_context("account"),
    # CloudFront's ACM cert must live in us-east-1; default there.
    region=app.node.try_get_context("region") or "us-east-1")

domain = app.node.try_get_context("domain") or "schema.keri.host"
zone_id = app.node.try_get_context("hosted_zone_id") or "ZPLACEHOLDER"

# allowlist may arrive as a JSON string (via --context) or already a list.
_allowlist_ctx = app.node.try_get_context("allowlist")
if isinstance(_allowlist_ctx, str):
    allowlist = json.loads(_allowlist_ctx)
else:
    allowlist = _allowlist_ctx or []

# witnesses may arrive as a JSON string or a list; toad is an int.
_witnesses_ctx = app.node.try_get_context("witnesses")
if isinstance(_witnesses_ctx, str):
    witnesses = json.loads(_witnesses_ctx)
else:
    witnesses = _witnesses_ctx or []
toad = int(app.node.try_get_context("toad") or 0)

# Shared pooled Tier-1 KERI-state table (KEL/TEL, namespaced per service).
core = KeriCoreStack(app, "KeriCore", table_name="keri-core", env=env)

# compute_code = this example dir (schema_host_handler.py + schema/). Inject the
# shim (robust handler resolution) before Code.from_asset stages it.
_asset_dir = str(pathlib.Path(__file__).parent)
inject_handler_shim(_asset_dir)

# The single stack: publish Service-AID + dedicated CAS bucket + CloudFront. The
# Lambda writes to exactly the bucket CloudFront serves (intra-stack, acyclic).
host = SchemaHostStack(
    app, "SchemaHost",
    alias="schema-publisher",
    core_table=core.table,
    compute_code=_lambda.Code.from_asset(_asset_dir),
    handler_ref="schema_host_handler:svc",
    domain_name=domain,
    hosted_zone_id=zone_id,
    witnesses=witnesses,
    toad=toad,
    allowlist=allowlist,
    env=env,
)

host.add_dependency(core)
app.synth()
