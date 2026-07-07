"""Integration gate: the schema.keri.host CDK app must SYNTHESIZE with no
circular-dependency error. This is the proof that the single-stack refactor
resolves the two-stack cycle (SchemaHost <-> service stack)."""
import os
import pathlib
import subprocess
import sys


def test_app_synthesizes():
    root = pathlib.Path(__file__).resolve().parents[2]
    app = root / "examples" / "schema_host" / "app.py"
    result = subprocess.run(
        [sys.executable, str(app)],
        cwd=str(root),
        env={
            "PYTHONPATH": str(root),
            "CDK_DEFAULT_ACCOUNT": "111111111111",
            "CDK_DEFAULT_REGION": "us-east-1",
            "PATH": os.environ["PATH"],
        },
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # A CIRCULAR STACK DEPENDENCY is the specific failure the single-stack
        # refactor exists to prevent — surface it loudly.
        assert "cycl" not in result.stderr.lower() and \
               "circular" not in result.stderr.lower(), \
               f"CIRCULAR DEPENDENCY in synth:\n{result.stderr}"
    assert result.returncode == 0, result.stderr
