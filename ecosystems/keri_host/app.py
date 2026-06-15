"""CDK app: keri.host ecosystem — KeriCoreStack + witness + mailbox.

Both witness and mailbox pool onto the shared KeriCoreStack table (LeadingKeys-scoped
to their respective stack namespaces: kel for witness, mbx for mailbox).

Synth without context (uses fallback defaults):
    python app.py

Real deploy (pass context via -c flags to cdk CLI):
    cdk deploy --all \
        -c witness_domain=witness.keri.host \
        -c mailbox_domain=mailbox.keri.host \
        -c hosted_zone_id=ZXXXXXXXXXXXXXXXXX \
        -c region=us-east-1
"""
import pathlib
import sys

# keri_cdk lives at the repo root and is not pip-installed, so put the repo root
# (ecosystems/keri_host -> ecosystems -> repo root) on sys.path before
# importing it. `cdk synth` runs this from the ecosystem dir, so cwd is not enough.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import aws_cdk as cdk

from keri_cdk import WitnessStack, MailboxStack, KeriCoreStack

app = cdk.App()
ctx = app.node.try_get_context
region = ctx("region") or "us-east-1"
env = cdk.Environment(region=region)

witness_domain = ctx("witness_domain") or "witness.example.com"
mailbox_domain = ctx("mailbox_domain") or "mailbox.example.com"
hosted_zone_id = ctx("hosted_zone_id") or "Z000000000000000"

# Service names (used as the Lambda function-name / REST API-name prefix).
# Override via -c flags for parallel/temporary deploys (e.g. witness-b/mailbox-b).
witness_name = ctx("witness_name") or "witness"
mailbox_name = ctx("mailbox_name") or "mailbox"

# Optional override for the AWS Lambda Web Adapter layer ARN (version pinned in
# the stack default); lets a deploy pass a current published version.
lwa_layer_arn = ctx("lwa_layer_arn")

core = KeriCoreStack(app, "KeriHostCore", table_name="keri-core", env=env)

WitnessStack(app, "KeriHostWitness",
             name=witness_name,
             alias="witness",
             domain_name=witness_domain,
             hosted_zone_id=hosted_zone_id,
             witness_url=f"https://{witness_domain}",
             core_table=core.table,
             env=env)

MailboxStack(app, "KeriHostMailbox",
             name=mailbox_name,
             alias="mailbox",
             domain_name=mailbox_domain,
             hosted_zone_id=hosted_zone_id,
             mailbox_url=f"https://{mailbox_domain}",
             core_table=core.table,
             lwa_layer_arn=lwa_layer_arn,
             env=env)

app.synth()
