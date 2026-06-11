"""CDK app: shared core stack + the Rating Engine Service AID."""
import aws_cdk as cdk
from serviceaid.cdk import KeriCoreStack, ServiceAid

# The 5-witness federation (see project memory reference_witness_federation).
WITNESSES = [
    # "BWit1...", "BWit2...", "BWit3...", "BWit4...", "BWit5...",
]

app = cdk.App()
core = KeriCoreStack(app, "KeriCore", table_name="keri-core")

rating = cdk.Stack(app, "RatingEngine")
ServiceAid(
    rating, "Rating",
    alias="rating",
    core_table_name="keri-core",
    handler_module="rating_handler",   # bootstrap imports this module name
    witnesses=WITNESSES,
    toad=max(0, (len(WITNESSES) * 2 + 2) // 3) if WITNESSES else 0,
    image_directory=".",               # Docker context = cwd at synth time; run `cdk deploy` from service-aid/
)
rating.add_dependency(core)

app.synth()
