# -*- encoding: utf-8 -*-
"""
Regression test for the clone/replay CESR genus-version mismatch.

The clone/replay serialization (``Baser.cloneEvtMsg`` / ``clonePreIter``), which
is what a witness's ``/oobi/{aid}/controller`` endpoint and the mailbox replay
emit, encloses its attachments in a CESR **v1** ``AttachmentGroup`` (``-V``) on an
otherwise **v2** message body (``gvrsn=2.0``). The default network ``Parser``
defaults to v2, where ``-V`` decodes as ``BackerRegistrarSealCouples``. A parser
that does not switch counter genus per attachment group therefore misreads the
clone attachment, fails to extract the controller signature group, and drops the
inception event as "Missing attached signature(s)" — so the resolver never stores
the KEL / loc / end-role reply records and ``fetchUrls`` comes back empty.

This test asserts the network Parser can ingest a clone/replay stream.
"""

from keri.app import habbing
from keri.core import eventing, parsing
from keri.core.signing import Salter


def test_default_parser_ingests_clone_replay_stream():
    """A clone/replay stream (v2 body + v1 AttachmentGroup) must be accepted by
    the default (v2) network Parser — this is the witness controller-OOBI path."""
    salt = Salter(raw=b'0123456789abcdef').qb64

    # Producer: a non-transferable AID (like a demo witness) whose KEL is served
    # over its /oobi/{aid}/controller endpoint via the clone/replay path.
    with habbing.openHby(name="producer", temp=True, salt=salt) as phby:
        hab = phby.makeHab(name="wit", transferable=False)
        clone = bytearray()
        for msg in phby.db.clonePreIter(pre=hab.pre):
            clone.extend(msg)

        # Resolver: fresh keystore, default network parser (defaults to v2).
        with habbing.openHby(name="resolver", temp=True) as rhby:
            kvy = eventing.Kevery(db=rhby.db, lax=True, local=False)
            parsing.Parser(kvy=kvy).parse(ims=bytearray(clone))
            kvy.processEscrows()

            assert hab.pre in rhby.kevers, (
                "clone/replay stream was not ingested by the default Parser "
                "(v1 AttachmentGroup misread under v2 counter table)"
            )
