# -*- encoding: utf-8 -*-
"""
tests.app.test_processing module

Tests for Processer and Processage classes.
"""
import pytest

from keri import core
from keri.core import coring, eventing, parsing

from keri.app import habbing, processing
from keri.app.processing import Processer, Processage, _mergeProcessages


class TestProcessage:
    """Tests for Processage namedtuple."""

    def test_processage_fields(self):
        """Verify Processage has expected fields."""
        p = Processage(outbound=[], queries=[], notifications=[])
        assert p.outbound == []
        assert p.queries == []
        assert p.notifications == []

    def test_merge_processages(self):
        """Test merging multiple Processage results."""
        p1 = Processage(outbound=[b"a"], queries=[{"q": 1}],
                         notifications=[{"n": 1}])
        p2 = Processage(outbound=[b"b"], queries=[],
                         notifications=[{"n": 2}])
        merged = _mergeProcessages(p1, p2)
        assert merged.outbound == [b"a", b"b"]
        assert merged.queries == [{"q": 1}]
        assert merged.notifications == [{"n": 1}, {"n": 2}]

    def test_merge_empty(self):
        """Test merging zero processages."""
        merged = _mergeProcessages()
        assert merged.outbound == []
        assert merged.queries == []
        assert merged.notifications == []


class TestProcesser:
    """Tests for Processer class."""

    def test_process_empty(self):
        """Processing an empty buffer returns empty Processage."""
        salt = core.Salter(raw=b'0123456789abcdef').qb64
        with habbing.openHby(name="proc_empty", temp=True, salt=salt) as hby:
            hab = hby.makeHab("local")
            proc = Processer(hby, hab=hab)

            # Drain any cues left over from makeHab's inception processing
            proc._drainCues()

            result = proc.process(ims=bytearray())
            assert isinstance(result, Processage)
            assert result.outbound == []
            assert result.queries == []
            assert result.notifications == []

    def test_process_inception(self):
        """Process inception event from a remote Hab.

        Creates two Haberies — one local and one remote. The remote Hab
        creates an inception event, which is then processed by the local
        Processer. The receipt should appear in outbound.
        """
        salt = core.Salter(raw=b'0123456789abcdef').qb64

        with habbing.openHby(name="proc_local", temp=True, salt=salt) as localHby, \
             habbing.openHby(name="proc_remote", temp=True, salt=salt) as remoteHby:

            # Create local hab (receipts will be signed by this)
            localHab = localHby.makeHab("local")

            # Create remote hab (generates the inception event)
            remoteHab = remoteHby.makeHab("remote")

            # Get the inception event from the remote hab
            icp = remoteHab.makeOwnInception()

            # Set up Processer for local side to process remote events
            proc = Processer(localHby, hab=localHab, local=False)

            # Process the remote inception
            result = proc.process(ims=bytearray(icp))

            assert isinstance(result, Processage)

            # The remote prefix should now be in local kevers
            assert remoteHab.pre in localHby.kevers

            # Should have generated a receipt cue → outbound message
            assert len(result.outbound) > 0

    def test_process_interaction(self):
        """Process an interaction event from a remote Hab."""
        salt = core.Salter(raw=b'0123456789abcdef').qb64

        with habbing.openHby(name="ixn_local", temp=True, salt=salt) as localHby, \
             habbing.openHby(name="ixn_remote", temp=True, salt=salt) as remoteHby:

            localHab = localHby.makeHab("local")
            remoteHab = remoteHby.makeHab("remote")

            # First process inception so local knows remote key state
            icp = remoteHab.makeOwnInception()
            proc = Processer(localHby, hab=localHab, local=False)
            proc.process(ims=bytearray(icp))
            assert remoteHab.pre in localHby.kevers

            # Create an interaction event
            remoteHab.interact()
            ixn = remoteHab.makeOwnEvent(sn=1)

            # Process the interaction
            result = proc.process(ims=bytearray(ixn))
            assert isinstance(result, Processage)

            # Verify kever updated
            kever = localHby.kevers[remoteHab.pre]
            assert kever.sn == 1

    def test_process_rotation(self):
        """Process a rotation event from a remote Hab."""
        salt = core.Salter(raw=b'0123456789abcdef').qb64

        with habbing.openHby(name="rot_local", temp=True, salt=salt) as localHby, \
             habbing.openHby(name="rot_remote", temp=True, salt=salt) as remoteHby:

            localHab = localHby.makeHab("local")
            remoteHab = remoteHby.makeHab("remote")

            # Process inception
            icp = remoteHab.makeOwnInception()
            proc = Processer(localHby, hab=localHab, local=False)
            proc.process(ims=bytearray(icp))

            # Rotate
            remoteHab.rotate()
            rot = remoteHab.makeOwnEvent(sn=1)

            result = proc.process(ims=bytearray(rot))
            assert isinstance(result, Processage)

            # Verify kever shows rotation
            kever = localHby.kevers[remoteHab.pre]
            assert kever.sn == 1

    def test_process_escrows_empty(self):
        """processEscrows on clean state returns empty Processage."""
        salt = core.Salter(raw=b'0123456789abcdef').qb64
        with habbing.openHby(name="esc_empty", temp=True, salt=salt) as hby:
            hab = hby.makeHab("local")
            proc = Processer(hby, hab=hab)

            # Drain any cues left over from makeHab's inception processing
            proc._drainCues()

            result = proc.processEscrows()
            assert isinstance(result, Processage)
            assert result.outbound == []
            assert result.queries == []
            assert result.notifications == []

    def test_processOnce_merges(self):
        """processOnce returns merged result of process + processEscrows."""
        salt = core.Salter(raw=b'0123456789abcdef').qb64

        with habbing.openHby(name="once_local", temp=True, salt=salt) as localHby, \
             habbing.openHby(name="once_remote", temp=True, salt=salt) as remoteHby:

            localHab = localHby.makeHab("local")
            remoteHab = remoteHby.makeHab("remote")
            icp = remoteHab.makeOwnInception()

            proc = Processer(localHby, hab=localHab, local=False)
            result = proc.processOnce(ims=bytearray(icp))

            assert isinstance(result, Processage)
            assert remoteHab.pre in localHby.kevers

    def test_processer_no_hab(self):
        """Processer without explicit hab finds one from hby.habs."""
        salt = core.Salter(raw=b'0123456789abcdef').qb64

        with habbing.openHby(name="nohab_local", temp=True, salt=salt) as localHby, \
             habbing.openHby(name="nohab_remote", temp=True, salt=salt) as remoteHby:

            localHab = localHby.makeHab("local")
            remoteHab = remoteHby.makeHab("remote")
            icp = remoteHab.makeOwnInception()

            # No explicit hab — Processer should find localHab from hby.habs
            proc = Processer(localHby, local=False)
            result = proc.process(ims=bytearray(icp))

            assert isinstance(result, Processage)
            assert remoteHab.pre in localHby.kevers
            # Should still generate receipt via discovered hab
            assert len(result.outbound) > 0

    def test_cue_categorization_query(self):
        """Verify query cues land in queries bucket."""
        salt = core.Salter(raw=b'0123456789abcdef').qb64
        with habbing.openHby(name="cue_q", temp=True, salt=salt) as hby:
            hab = hby.makeHab("local")
            proc = Processer(hby, hab=hab)

            # Drain any pre-existing cues from makeHab
            proc._drainCues()

            # Manually push a query cue into kevery's cue deck
            proc.kevery.cues.push(dict(kin="query",
                                       q=dict(pre="ETest", sn="0")))
            proc.kevery.cues.push(dict(kin="telquery",
                                       q=dict(ri="ERegistryTest")))

            result = proc._drainCues()
            assert len(result.queries) == 2
            assert result.queries[0]["kin"] == "query"
            assert result.queries[1]["kin"] == "telquery"
            assert result.outbound == []
            assert result.notifications == []

    def test_cue_categorization_notification(self):
        """Verify unknown cue types land in notifications bucket."""
        salt = core.Salter(raw=b'0123456789abcdef').qb64
        with habbing.openHby(name="cue_n", temp=True, salt=salt) as hby:
            hab = hby.makeHab("local")
            proc = Processer(hby, hab=hab)

            # Drain pre-existing cues from makeHab
            proc._drainCues()

            proc.kevery.cues.push(dict(kin="keyStateSaved",
                                       ksn={"test": True}))
            proc.kevery.cues.push(dict(kin="notice",
                                       serder=None))

            result = proc._drainCues()
            assert len(result.notifications) == 2
            assert result.notifications[0]["kin"] == "keyStateSaved"
            assert result.notifications[1]["kin"] == "notice"

    def test_cue_categorization_replay(self):
        """Verify replay cues land in outbound bucket."""
        salt = core.Salter(raw=b'0123456789abcdef').qb64
        with habbing.openHby(name="cue_r", temp=True, salt=salt) as hby:
            hab = hby.makeHab("local")
            proc = Processer(hby, hab=hab)

            # Drain pre-existing cues from makeHab
            proc._drainCues()

            msgs = bytearray(b"fake-replay-data")
            proc.kevery.cues.push(dict(kin="replay", msgs=msgs))

            result = proc._drainCues()
            assert len(result.outbound) == 1
            assert result.outbound[0] == msgs
