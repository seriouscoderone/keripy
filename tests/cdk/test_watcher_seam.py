import pytest
import aws_cdk as cdk
from keri_cdk import WatcherStack, KeriCoreStack


def test_watcher_is_seam_not_implemented():
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region="us-east-1")
    core = KeriCoreStack(app, "Core", table_name="keri-core", env=env)
    with pytest.raises(NotImplementedError):
        WatcherStack(app, "Wat", name="watcher", domain_name="wat.ex.com",
                     hosted_zone_id="Z123ABC456DEF7", core_table=core.table, env=env)


def test_watcher_in_exports():
    import keri_cdk
    assert "WatcherStack" in keri_cdk.__all__
