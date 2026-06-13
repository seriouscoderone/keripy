import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from keri_cdk import KeriRuntimeLayer


def test_layer_is_arm64_python():
    app = cdk.App()
    stack = cdk.Stack(app, "S")
    KeriRuntimeLayer(stack, "Layer")
    t = Template.from_stack(stack)
    # keripy's setup.py pins python_requires='>=3.14.0', so the layer targets
    # the python3.14 managed runtime (python3.13 cannot pip-install keri). The
    # plan was drafted before this constraint surfaced; python3.14 is GA in
    # Lambda (Nov 2025) and supported by CDK 2.259.0.
    t.has_resource_properties("AWS::Lambda::LayerVersion", {
        "CompatibleArchitectures": ["arm64"],
        "CompatibleRuntimes": Match.array_with(["python3.14"]),
    })
