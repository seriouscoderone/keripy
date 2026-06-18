"""CDK app: keri.host federation — KeriCoreStack + one witness+mailbox pair per domain.

Synth without context (falls back to federation.example.json):
    python app.py
Real deploy (federation.json present, or $KERI_HOST_FEDERATION set):
    AWS_PROFILE=personal npx aws-cdk@latest deploy --all -c region=us-east-1
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import aws_cdk as cdk

from keri_cdk.federation import load_federation, build_federation

app = cdk.App()
region = app.node.try_get_context("region") or "us-east-1"
env = cdk.Environment(region=region)
lwa_layer_arn = app.node.try_get_context("lwa_layer_arn")

entries = load_federation(pathlib.Path(__file__).resolve().parent)
build_federation(app, entries, env, lwa_layer_arn=lwa_layer_arn)

app.synth()
