"""Tests for the pure selection/classification logic of teardown_sam."""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[2] / "ecosystems" / "keri_host" / "teardown_sam.py"
_spec = importlib.util.spec_from_file_location("teardown_sam", _PATH)
teardown_sam = importlib.util.module_from_spec(_spec)


def setup_module(_):
    _spec.loader.exec_module(teardown_sam)


def _summary(name, status="CREATE_COMPLETE"):
    return {"StackName": name, "StackStatus": status}


def test_selects_and_classifies_federation_stacks():
    summaries = [
        _summary("serverless-witness"),
        _summary("serverless-mailbox"),
        _summary("serverless-witness-honest"),
        _summary("serverless-mailbox-legitim"),
        _summary("serverless-witness-abc123-CompanionStack"),
        _summary("serverless-mailbox-def456-CompanionStack"),
        _summary("some-other-stack"),                 # not ours -> ignored
        _summary("serverless-old", status="DELETE_COMPLETE"),  # already gone -> ignored
    ]
    sel = teardown_sam.select_sam_stacks(summaries)
    assert set(sel["functional"]) == {
        "serverless-witness", "serverless-mailbox",
        "serverless-witness-honest", "serverless-mailbox-legitim",
    }
    assert set(sel["companion"]) == {
        "serverless-witness-abc123-CompanionStack",
        "serverless-mailbox-def456-CompanionStack",
    }
    assert "some-other-stack" not in sel["functional"] + sel["companion"]
    assert "serverless-old" not in sel["functional"] + sel["companion"]


def test_format_plan_lists_every_selected_stack():
    sel = {"functional": ["serverless-witness"], "companion": ["serverless-x-CompanionStack"]}
    text = teardown_sam.format_plan(sel)
    assert "serverless-witness" in text
    assert "serverless-x-CompanionStack" in text
