# -*- encoding: utf-8 -*-
"""
Regression test for mailbox query serialization kind.

A mailbox query carries a ``topics`` map whose keys are route-like strings
(e.g. ``/receipt``, ``/replay``). Those keys are not valid native-CESR field
labels, so a query that carries them cannot be serialized as native CESR and
must be JSON (as it was before the CESR v2 refactor).

The refactor flipped ``eventing.query``'s default ``kind`` from ``Kinds.json``
to native CESR (``Kind = Kinds.cesr``), so building a mailbox query -- which
``MailboxDirector`` does while ``kli ends add`` runs -- raised
``SerializeError("Invalid value while serializing")`` (the ``Mapper`` rejecting
the ``/receipt`` label), crashing the command.
"""

from keri.core import eventing
from keri.core.serdering import SerderKERI
from keri.kering import Kinds


PRE = "BBilc4-L3tFUnfM_wJr4S4OJanAv_VmF_dJNN6vkf2Ha"  # valid 44-char qb64 AID


def test_mailbox_query_with_topics_serializes_as_json():
    """A query carrying a topics map must serialize (as JSON) without raising."""
    serder = eventing.query(
        pre=PRE,
        route="mbx",
        query={"i": PRE, "topics": {"/receipt": 0, "/replay": 0, "/multisig": 0}},
    )
    assert serder.said  # constructed and SAIDified without raising
    assert serder.kind == Kinds.json  # topic maps cannot be native CESR
    # round-trips: the raw serialization parses back to the same SAID
    assert SerderKERI(raw=serder.raw).said == serder.said
