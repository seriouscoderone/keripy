"""Tests for harvest_aids extraction/shaping (no network)."""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[2] / "ecosystems" / "keri_host" / "harvest_aids.py"
_spec = importlib.util.spec_from_file_location("harvest_aids", _PATH)
harvest_aids = importlib.util.module_from_spec(_spec)


def setup_module(_):
    _spec.loader.exec_module(harvest_aids)


def test_extract_aid_from_oobi_payload():
    payload = {"i": "BEHIsEX9_witness_aid_placeholder_0000000000", "role": "witness"}
    assert harvest_aids.extract_aid(payload, "witness").startswith("BEHIsEX9")


def test_harvest_shapes_per_slug_with_url():
    entries = [{"slug": "Alpha", "domain": "alpha.test", "hosted_zone_id": "Z1"}]

    def fake_fetch(url):
        # witness vs mailbox distinguished by subdomain in the URL
        aid = "BWIT_alpha" if url.startswith("https://witness.") else "BMBX_alpha"
        return {"i": aid}

    out = harvest_aids.harvest(entries, fake_fetch)
    assert out["witnesses"]["Alpha"] == {"aid": "BWIT_alpha", "url": "https://witness.alpha.test"}
    assert out["mailboxes"]["Alpha"] == {"aid": "BMBX_alpha", "url": "https://mailbox.alpha.test"}
