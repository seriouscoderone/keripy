"""The witness/mailbox handlers resolve their pooled-table namespace from env."""
import importlib

# _namespace() reads os.environ at call time (not at import), so import_module
# returning a cached module instance is fine — monkeypatch.setenv still takes
# effect on each call.


def test_witness_namespace_from_env(monkeypatch):
    monkeypatch.setenv("WITNESS_NAMESPACE", "KeriHostWitness:kel")
    wh = importlib.import_module("keri_cdk.handlers.witness.witness_handler")
    assert wh._namespace("witness") == "KeriHostWitness:kel"


def test_witness_namespace_default(monkeypatch):
    monkeypatch.delenv("WITNESS_NAMESPACE", raising=False)
    wh = importlib.import_module("keri_cdk.handlers.witness.witness_handler")
    assert wh._namespace("witness") == "witness:kel"


def test_mailbox_namespace_from_env(monkeypatch):
    monkeypatch.setenv("MAILBOX_NAMESPACE", "KeriHostMailbox:mbx")
    mh = importlib.import_module("keri_cdk.handlers.mailbox.mailbox_handler")
    assert mh._namespace("mailbox") == "KeriHostMailbox:mbx"


def test_mailbox_namespace_default(monkeypatch):
    monkeypatch.delenv("MAILBOX_NAMESPACE", raising=False)
    mh = importlib.import_module("keri_cdk.handlers.mailbox.mailbox_handler")
    assert mh._namespace("mailbox") == "mailbox:mbx"


def test_witness_namespace_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("WITNESS_NAMESPACE", "")
    wh = importlib.import_module("keri_cdk.handlers.witness.witness_handler")
    assert wh._namespace("witness") == "witness:kel"


def test_mailbox_namespace_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MAILBOX_NAMESPACE", "")
    mh = importlib.import_module("keri_cdk.handlers.mailbox.mailbox_handler")
    assert mh._namespace("mailbox") == "mailbox:mbx"
