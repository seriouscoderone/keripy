"""Tests for keri_cdk.federation.load_federation (config resolution + validation)."""
import pathlib

import pytest

from keri_cdk.federation import load_federation

ECOSYSTEM_DIR = pathlib.Path(__file__).resolve().parents[2] / "ecosystems" / "keri_host"


def test_loads_committed_example_with_five_entries():
    # No env var, no gitignored federation.json in a clean checkout -> falls back to example.
    entries = load_federation(ECOSYSTEM_DIR, env={})
    assert len(entries) == 5
    for e in entries:
        assert {"slug", "domain", "hosted_zone_id"} <= set(e)
        assert e["domain"].endswith("example.com")  # placeholders only, never real domains


def test_inline_env_json_overrides_files(tmp_path):
    inline = '[{"slug":"X","domain":"x.test","hosted_zone_id":"Z1"}]'
    entries = load_federation(tmp_path, env={"KERI_HOST_FEDERATION": inline})
    assert entries == [{"slug": "X", "domain": "x.test", "hosted_zone_id": "Z1"}]


def test_env_path_is_read_when_not_inline(tmp_path):
    p = tmp_path / "fed.json"
    p.write_text('[{"slug":"Y","domain":"y.test","hosted_zone_id":"Z2"}]')
    entries = load_federation(tmp_path, env={"KERI_HOST_FEDERATION": str(p)})
    assert entries[0]["slug"] == "Y"


def test_real_file_preferred_over_example(tmp_path):
    (tmp_path / "federation.json").write_text('[{"slug":"R","domain":"r.t","hosted_zone_id":"Z3"}]')
    (tmp_path / "federation.example.json").write_text('[{"slug":"E","domain":"e.t","hosted_zone_id":"Z4"}]')
    entries = load_federation(tmp_path, env={})
    assert entries[0]["slug"] == "R"


def test_rejects_duplicate_slug(tmp_path):
    inline = '[{"slug":"X","domain":"a","hosted_zone_id":"Z1"},{"slug":"X","domain":"b","hosted_zone_id":"Z2"}]'
    with pytest.raises(ValueError, match="duplicate slug"):
        load_federation(tmp_path, env={"KERI_HOST_FEDERATION": inline})


def test_rejects_missing_key(tmp_path):
    with pytest.raises(ValueError, match="missing keys"):
        load_federation(tmp_path, env={"KERI_HOST_FEDERATION": '[{"slug":"X","domain":"a"}]'})


def test_rejects_empty_list(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        load_federation(tmp_path, env={"KERI_HOST_FEDERATION": "[]"})
