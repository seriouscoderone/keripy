import pytest
from keri_serviceaid.egf.errors import OobiNotFound
from keri_serviceaid.egf.oobi_source import HttpOobiEndpointSource, LocalDirOobiSource

AID = "E" + "A" * 43


def test_fetch_reads_cesr_bytes(tmp_path):
    (tmp_path / "oobis").mkdir()
    (tmp_path / "oobis" / f"{AID}.cesr").write_bytes(b"raw-cesr-stream")
    assert LocalDirOobiSource(tmp_path).fetch(AID) == b"raw-cesr-stream"


def test_missing_raises_oobi_not_found(tmp_path):
    (tmp_path / "oobis").mkdir()
    with pytest.raises(OobiNotFound) as ei:
        LocalDirOobiSource(tmp_path).fetch(AID)
    assert ei.value.aid == AID


def test_path_traversal_guard(tmp_path):
    with pytest.raises(OobiNotFound):
        LocalDirOobiSource(tmp_path).fetch("../etc/passwd")


def test_http_source_is_declared_stub():
    with pytest.raises(NotImplementedError):
        HttpOobiEndpointSource("https://egf.usurance.com").fetch(AID)
