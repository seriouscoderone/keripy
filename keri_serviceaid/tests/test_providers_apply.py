# -*- encoding: utf-8 -*-
"""IPEX apply framing + sent-apply enumeration. Framing mirrors _frame_grant's
v1 pin; persistence requires the host to parse the frame into its own
Exchanger (Exchanger.processEvent persists unconditionally on valid sigs)."""
from keri.app import habbing
from keri.core import parsing
from keri.kering import Vrsn_1_0
from keri.peer import exchanging

from keri_serviceaid.providers import frame_apply_for, list_sent_applies

SCHEMA = "E" + "A" * 43


class Capture:
    def __init__(self):
        self.events = []

    def on_event(self, source, event_type, data):
        self.events.append((source, event_type, data))


def _persist(hby, raw):
    exc = exchanging.Exchanger(hby=hby, handlers=[])
    parsing.Parser().parseOne(ims=bytearray(raw), exc=exc, version=Vrsn_1_0)


def test_frame_apply_returns_said_and_raw_and_emits_sink():
    with habbing.openHby(name="apl1", temp=True) as hby:
        hab = hby.makeHab(name="apl1")
        sink = Capture()
        said, raw = frame_apply_for(hby, hab, schema_said=SCHEMA,
                                    recipient=hab.pre, message="please",
                                    sink=sink, return_raw=True)
        assert said.startswith("E") and isinstance(raw, bytes) and raw
        assert sink.events == [("ApplyFlow", "apply_framed",
                                {"said": said, "schema_said": SCHEMA,
                                 "recipient": hab.pre})]


def test_sent_apply_persists_and_enumerates():
    with habbing.openHby(name="apl2", temp=True) as hby:
        hab = hby.makeHab(name="apl2")
        said, raw = frame_apply_for(hby, hab, schema_said=SCHEMA,
                                    recipient=hab.pre, message="please",
                                    return_raw=True)
        assert list_sent_applies(hby, hab.pre) == []   # framing alone: nothing
        _persist(hby, raw)
        rows = list_sent_applies(hby, hab.pre)
        assert [r["said"] for r in rows] == [said]
        assert rows[0]["schema_said"] == SCHEMA
        assert rows[0]["recipient"] == hab.pre
        assert rows[0]["message"] == "please"
        assert rows[0]["dt"]


def test_list_sent_applies_filters_route_and_sender():
    with habbing.openHby(name="apl3", temp=True) as hby:
        me = hby.makeHab(name="me")
        other = hby.makeHab(name="other")
        _, raw_mine = frame_apply_for(hby, me, schema_said=SCHEMA,
                                      recipient=other.pre, return_raw=True)
        _, raw_theirs = frame_apply_for(hby, other, schema_said=SCHEMA,
                                        recipient=me.pre, return_raw=True)
        _persist(hby, raw_mine)
        _persist(hby, raw_theirs)
        assert len(list_sent_applies(hby, me.pre)) == 1
        assert len(list_sent_applies(hby, other.pre)) == 1
