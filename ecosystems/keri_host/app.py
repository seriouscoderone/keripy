"""CDK app: keri.host ecosystem — witness + mailbox only (no core table, no Service AID).

Each stack owns its own Baser table ({name}-db). No KeriCoreStack dependency.

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

from keri_cdk import WitnessStack, MailboxStack

app = cdk.App()
ctx = app.node.try_get_context
region = ctx("region") or "us-east-1"
env = cdk.Environment(region=region)

witness_domain = ctx("witness_domain") or "witness.example.com"
mailbox_domain = ctx("mailbox_domain") or "mailbox.example.com"
hosted_zone_id = ctx("hosted_zone_id") or "Z000000000000000"

# Table names ({name}-db) are context-overridable so a parallel/temporary
# deploy can use distinct names (e.g. wit/mbox) without colliding with a live
# witness-db/mailbox-db. Defaults unchanged so synth tests stay green.
witness_name = ctx("witness_name") or "witness"
mailbox_name = ctx("mailbox_name") or "mailbox"

# Optional override for the AWS Lambda Web Adapter layer ARN (version pinned in
# the stack default); lets a deploy pass a current published version.
lwa_layer_arn = ctx("lwa_layer_arn")

WitnessStack(app, "KeriHostWitness",
             name=witness_name,
             alias="witness",
             domain_name=witness_domain,
             hosted_zone_id=hosted_zone_id,
             witness_url=f"https://{witness_domain}",
             env=env)

MailboxStack(app, "KeriHostMailbox",
             name=mailbox_name,
             alias="mailbox",
             domain_name=mailbox_domain,
             hosted_zone_id=hosted_zone_id,
             mailbox_url=f"https://{mailbox_domain}",
             lwa_layer_arn=lwa_layer_arn,
             env=env)

app.synth()
