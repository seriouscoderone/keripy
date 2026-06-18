"""Tests for the e2e client's pure config builders (no kli, no network)."""
import importlib.util
import pathlib

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[2] / "ecosystems" / "keri_host" / "e2e_client.py"
_spec = importlib.util.spec_from_file_location("e2e_client", _PATH)
e2e_client = importlib.util.module_from_spec(_spec)


def setup_module(_):
    _spec.loader.exec_module(e2e_client)


_AIDS = {
    "witnesses": {
        "Alpha":   {"aid": "BWIT_a", "url": "https://witness.alpha.test"},
        "Bravo":   {"aid": "BWIT_b", "url": "https://witness.bravo.test"},
        "Charlie": {"aid": "BWIT_c", "url": "https://witness.charlie.test"},
        "Delta":   {"aid": "BWIT_d", "url": "https://witness.delta.test"},
        "Echo":    {"aid": "BWIT_e", "url": "https://witness.echo.test"},
    },
    "mailboxes": {"Alpha": {"aid": "BMBX_a", "url": "https://mailbox.alpha.test"}},
}


def test_build_incept_config_three_of_five():
    cfg = e2e_client.build_incept_config(_AIDS, toad=3)
    assert len(cfg["wits"]) == 5
    assert set(cfg["wits"]) == {"BWIT_a", "BWIT_b", "BWIT_c", "BWIT_d", "BWIT_e"}
    assert cfg["toad"] == 3
    assert cfg["transferable"] is True


def test_witness_oobis_built_per_witness():
    oobis = e2e_client.witness_oobis(_AIDS)
    assert "https://witness.alpha.test/oobi/BWIT_a" in oobis
    assert len(oobis) == 5


def test_rejects_toad_above_witness_count():
    few = {"witnesses": {"Alpha": {"aid": "BWIT_a", "url": "u"}}, "mailboxes": {}}
    with pytest.raises(ValueError, match="toad"):
        e2e_client.build_incept_config(few, toad=3)
