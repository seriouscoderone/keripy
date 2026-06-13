import pytest
import aws_cdk as cdk
from keri_cdk import WatcherStack


def test_watcher_exported_and_is_seam():
    # the construct API exists (importable) but raises until built
    app = cdk.App()
    with pytest.raises(NotImplementedError):
        WatcherStack(app, "Watch", name="watcher", domain_name="w.example.com",
                     hosted_zone_id="Z123ABC456DEF7")


def test_watcher_in_exports():
    import keri_cdk
    assert "WatcherStack" in keri_cdk.__all__
