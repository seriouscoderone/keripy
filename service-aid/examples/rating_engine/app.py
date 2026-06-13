"""CDK app: shared core stack + the Rating Engine Service AID.

LEGACY example (superseded by examples/gated_retrieval as the canonical template;
the service-aid/ tree is removed in a later cutover task). Updated to the Phase B
ServiceAid signature: a cross-stack core table (core.table) + zip+layer Lambda.
"""
import pathlib
import sys

# keri_cdk lives at the repo root and is not pip-installed, so put the repo root
# (examples/rating_engine -> examples -> service-aid -> repo root) on sys.path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

import aws_cdk as cdk

from keri_cdk import KeriCoreStack, ServiceAid

# The 5-witness federation (see project memory reference_witness_federation).
WITNESSES = [
    # "BWit1...", "BWit2...", "BWit3...", "BWit4...", "BWit5...",
]

app = cdk.App()
env = cdk.Environment(region=app.node.try_get_context("region") or "us-east-1")
core = KeriCoreStack(app, "KeriCore", table_name="keri-core", env=env)

rating = cdk.Stack(app, "RatingEngine", env=env)
ServiceAid(
    rating, "Rating",
    alias="rating",
    core_table=core.table,             # cross-stack ref -> Export/Fn::ImportValue lock
    handler_module="rating_handler",   # runtime.init() imports this module name
    witnesses=WITNESSES,
    toad=max(0, (len(WITNESSES) * 2 + 2) // 3) if WITNESSES else 0,
)
rating.add_dependency(core)

app.synth()
