"""Tests for harvest_aids extraction/shaping (no network)."""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[2] / "ecosystems" / "keri_host" / "harvest_aids.py"
_spec = importlib.util.spec_from_file_location("harvest_aids", _PATH)
harvest_aids = importlib.util.module_from_spec(_spec)


def setup_module(_):
    _spec.loader.exec_module(harvest_aids)


def test_extract_aid_prefers_role_key():
    # The deployed witness/mailbox root keys the AID by role.
    assert harvest_aids.extract_aid({"witness": "BWIT_x", "alias": "w"}, "witness") == "BWIT_x"
    assert harvest_aids.extract_aid({"mailbox": "BMBX_x", "alias": "m"}, "mailbox") == "BMBX_x"


def test_extract_aid_falls_back_to_i():
    # A raw key-event / OOBI payload uses "i".
    assert harvest_aids.extract_aid({"i": "BEHIsEX9_aid"}, "witness") == "BEHIsEX9_aid"


def test_extract_aid_raises_when_absent():
    import pytest
    with pytest.raises(ValueError, match="no AID field"):
        harvest_aids.extract_aid({"alias": "w"}, "witness")


def test_harvest_shapes_per_slug_with_url():
    entries = [{"slug": "Alpha", "domain": "alpha.test", "hosted_zone_id": "Z1"}]

    def fake_fetch(url):
        # The role-keyed root shape; witness vs mailbox by subdomain in the URL.
        if url.startswith("https://witness."):
            return {"witness": "BWIT_alpha", "alias": "witness-alpha"}
        return {"mailbox": "BMBX_alpha", "alias": "mailbox-alpha"}

    out = harvest_aids.harvest(entries, fake_fetch)
    assert out["witnesses"]["Alpha"] == {"aid": "BWIT_alpha", "url": "https://witness.alpha.test"}
    assert out["mailboxes"]["Alpha"] == {"aid": "BMBX_alpha", "url": "https://mailbox.alpha.test"}
