"""Tests for the kerihost patch queue (see PATCHES.md). Each fails on plain upstream."""
from keri.kering import Schemes
from keri.app.habbing import openHab, openHby


def test_schemes_include_wss():
    """P1: wss is a known endpoint scheme."""
    assert Schemes.wss == "wss"
    assert "wss" in Schemes


def test_wss_loc_scheme_accepted():
    """P1: a /loc/scheme reply with scheme wss is accepted, not rejected at
    Kevery.processReplyLocScheme (core/eventing.py:5281)."""
    with openHab(name="sender", temp=True) as (_, sender), \
            openHby(name="provider", temp=True) as hby:
        provider = hby.makeHab(name="mbx", transferable=False)
        sender.psr.parse(ims=bytearray(provider.replay()))
        location = provider.makeLocScheme(url="wss://127.0.0.1:9999", scheme="wss")
        sender.psr.parse(ims=bytearray(location))
        loc = sender.db.locs.get(keys=(provider.pre, "wss"))
        assert loc is not None
        assert loc.url == "wss://127.0.0.1:9999"
