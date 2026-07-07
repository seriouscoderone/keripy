from keri.app import habbing
from keri.core.signing import Salter
from keri.vdr import credentialing

from keri_serviceaid import ServiceAid
from keri_serviceaid.local_runtime import LocalRuntime
from keri_serviceaid.providers.artifact_store import LocalArtifactStore


def test_service_aid_accepts_artifact_store():
    svc = ServiceAid(alias="schema-publisher", artifact_store=LocalArtifactStore())
    assert isinstance(svc.artifact_store, LocalArtifactStore)


def test_local_runtime_wires_default_local_artifact_store():
    hby = habbing.Habery(name="svc", temp=True, salt=Salter(raw=b'0123456789abcdef').qb64)
    hab = hby.makeHab(name="schema-publisher")
    rgy = credentialing.Regery(hby=hby, name="schema-publisher", temp=True)
    svc = ServiceAid(alias="schema-publisher")
    LocalRuntime(svc, hby=hby, hab=hab, rgy=rgy)
    assert isinstance(svc.artifact_store, LocalArtifactStore)
    hby.close()
