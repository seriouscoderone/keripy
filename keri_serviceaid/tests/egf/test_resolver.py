import json

import pytest
from keri.core import coring

from keri_serviceaid.egf.config import EgfConfig, make_resolver
from keri_serviceaid.egf.errors import EgfDocumentError, EgfIntegrityError, EgfNotFound
from keri_serviceaid.egf.resolver import EgfResolver
from keri_serviceaid.egf.source import HttpOobiSource, LocalDirSource
from keri_serviceaid.tests.egf.fixtures.make_fixture_egf import fixture_egf


@pytest.fixture
def egf_dir(tmp_path):
    said, sad = fixture_egf()
    (tmp_path / f"{said}.json").write_text(json.dumps(sad))
    return tmp_path, said


def test_resolves_and_types_egf(egf_dir):
    root, said = egf_dir
    doc = EgfResolver(LocalDirSource(root)).resolve_egf(said)
    assert doc.said == said and doc.personas()


def test_missing_artifact_raises_not_found(tmp_path):
    with pytest.raises(EgfNotFound):
        EgfResolver(LocalDirSource(tmp_path)).resolve_egf("E" + "Z" * 43)


def test_tampered_file_raises_integrity_never_partial(egf_dir):
    root, said = egf_dir
    p = root / f"{said}.json"
    p.write_text(p.read_text().replace("Which state?", "Which STATE?"))
    with pytest.raises(EgfIntegrityError):
        EgfResolver(LocalDirSource(root)).resolve_egf(said)


def test_cache_by_said_fetches_once(egf_dir, monkeypatch):
    root, said = egf_dir
    src = LocalDirSource(root); calls = []
    orig = src.fetch
    monkeypatch.setattr(src, "fetch", lambda s: (calls.append(s), orig(s))[1])
    r = EgfResolver(src); r.resolve_egf(said); r.resolve_egf(said)
    assert calls == [said]


def test_make_resolver_local(egf_dir):
    root, said = egf_dir
    cfg = EgfConfig(source="local", document_said=said, local_dir=root)
    assert make_resolver(cfg).resolve_egf(said).said == said


# --- resolve_schema: ACDC schema-versioning requires a `version` string ---

def _saidified_schema(extra: dict, tmp_path):
    schema = {"$id": ""}
    schema.update(extra)
    _, sad = coring.Saider.saidify(sad=schema, label="$id")
    said = sad["$id"]
    (tmp_path / f"{said}.json").write_text(json.dumps(sad))
    return said


def test_resolve_schema_without_version_raises_document_error(tmp_path):
    said = _saidified_schema({"title": "T"}, tmp_path)
    with pytest.raises(EgfDocumentError):
        EgfResolver(LocalDirSource(tmp_path)).resolve_schema(said)


def test_resolve_schema_with_version_resolves(tmp_path):
    said = _saidified_schema({"title": "T", "version": "1.0.0"}, tmp_path)
    resolved = EgfResolver(LocalDirSource(tmp_path)).resolve_schema(said)
    assert resolved["version"] == "1.0.0"


# --- resolve_micro_app: verified plain dict, label "d" ---

def test_resolve_micro_app_returns_verified_dict(tmp_path):
    doc = {"d": "", "id": "carrier-applies", "role_id": "carrier"}
    _, sad = coring.Saider.saidify(sad=doc, label="d")
    said = sad["d"]
    (tmp_path / f"{said}.json").write_text(json.dumps(sad))
    resolved = EgfResolver(LocalDirSource(tmp_path)).resolve_micro_app(said)
    assert resolved["id"] == "carrier-applies"


# --- HttpOobiSource: present but unimplemented (transport project #2) ---

def test_http_oobi_source_fetch_raises_not_implemented():
    with pytest.raises(NotImplementedError, match=r"transport project \(#2\)"):
        HttpOobiSource("https://example.test/oobi").fetch("E" + "A" * 43)


def test_make_resolver_https_source_wires_http_oobi(egf_dir):
    _, said = egf_dir
    cfg = EgfConfig(source="https://example.test/oobi", document_said=said)
    resolver = make_resolver(cfg)
    with pytest.raises(NotImplementedError):
        resolver.resolve_egf(said)


def test_make_resolver_unsupported_source_raises():
    with pytest.raises(ValueError):
        make_resolver(EgfConfig(source="s3://bucket/path"))
