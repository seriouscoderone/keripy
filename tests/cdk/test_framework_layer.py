"""ServiceAidFrameworkLayer synthesizes a python3.14/arm64 LayerVersion."""
import os
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match

from keri_cdk.framework_layer import ServiceAidFrameworkLayer


def _synth(tmp_path):
    # The asset dir must exist for Code.from_asset; point at a temp dir with a
    # placeholder so synth does not require a real layer build.
    asset = str(tmp_path / "fw")
    os.makedirs(os.path.join(asset, "python"), exist_ok=True)
    open(os.path.join(asset, "python", ".keep"), "w").close()
    app = cdk.App()
    stack = cdk.Stack(app, "S")
    ServiceAidFrameworkLayer(stack, "Fw", asset_path=asset)
    return Template.from_stack(stack)


def test_layer_runtime_and_arch(tmp_path):
    tmpl = _synth(tmp_path)
    tmpl.has_resource_properties("AWS::Lambda::LayerVersion", {
        "CompatibleRuntimes": Match.array_with(["python3.14"]),
        "CompatibleArchitectures": Match.array_with(["arm64"]),
    })
