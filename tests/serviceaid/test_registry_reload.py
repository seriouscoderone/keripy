"""Cold-start regression: a RELOADED registry must have `vcp` repopulated.

Regery.loadRegistries reconstructs Registry objects from the reger but historically
left `vcp=None`. Upstream's v2 Registry.issue() reads `self.vcp.pvrsn`/`self.vcp.kind`
(issueEvent version=...), so a cold-started process that LOADS (not makes) its
registry and then issues would raise AttributeError. This guards the repopulation.

Habs are auto-pinned v1 by the conftest `_hold_serviceaid_v1` fixture (the serviceaid
v1 hold), so the registry inception is a v1 `vcp` event.
"""
from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.vdr import credentialing

from keri_serviceaid.providers.issue import ensure_registry


def test_reloaded_registry_repopulates_vcp():
    hby = Habery(name="reg-reload", temp=True, salt=Salter(raw=b'0123456789abcdef').qb64)
    hab = hby.makeHab(name="reg-reload", transferable=True)     # v1 via the autouse hold
    rgy = credentialing.Regery(hby=hby, name="rr", temp=True)
    # Fully establish the registry (make + KEL anchor + complete) so its vcp lands in
    # the accepted TEL, exactly as the deploy-time inception does.
    registry = ensure_registry(hby, hab, rgy, name="rr")
    assert registry.vcp is not None                             # freshly made => vcp set

    # Cold-start reload: a NEW Regery over the SAME reger => setup()/loadRegistries
    # reconstructs the registry object from the db.
    rgy2 = credentialing.Regery(hby=hby, name="rr", reger=rgy.reger)
    reg2 = rgy2.registryByName("rr")
    assert reg2 is not None
    assert reg2.vcp is not None, "reloaded registry vcp must be repopulated"
    assert reg2.vcp.said == registry.vcp.said
    assert reg2.vcp.pvrsn == registry.vcp.pvrsn                 # what Registry.issue() reads
    assert reg2.regd == registry.regd
    hby.close()
