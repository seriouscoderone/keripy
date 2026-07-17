# -*- encoding: utf-8 -*-
"""
Regression test for exn (peer exchange) message serialization round-trip.

`kli exn send` builds an exn via `eventing.exchange(...)` carrying a caller
payload in its `a` attributes. The CESR v2 refactor flipped exchange()'s default
`kind` from JSON to native CESR (`Kind = Kinds.cesr`). Native-CESR round-trip of
an exn is broken in that build: empty optional fields (`ri`, `x`, `p`) are omitted
on serialization but expected positionally on deserialization, so re-parsing
raised `InvalidCodeError`/`DeserializeError` (e.g. reading the `dt` Dater code
`1AAG` where a Prefixer was expected). Peer messages therefore default to JSON.
"""

from keri.core import eventing
from keri.core.serdering import SerderKERI
from keri.kering import Kinds


SENDER = "EIbNoxg7CfW3-MbqlgsfwVA9NuzVr0Jt0Awl6lLKFUsf"  # valid 44-char qb64 AID


def test_exn_with_payload_roundtrips():
    """An exn with a caller payload must serialize (JSON) and parse back."""
    data = {
        "license_number": "P-12345",
        "jurisdiction": "US-UT",
        "lines_of_business": ["property"],
        "effective_date": "2026-01-01",
        "expiration_date": "2027-01-01",
    }
    exn = eventing.exchange(route="/insurance/cmd/grant_license",
                            attributes=data, sender=SENDER)
    assert exn.kind == Kinds.json  # exn carries an arbitrary attribute map
    # round-trips: re-parsing the raw yields the same SAID and payload
    reparsed = SerderKERI(raw=exn.raw)
    assert reparsed.said == exn.said
    assert reparsed.sad["a"]["license_number"] == "P-12345"
    assert reparsed.sad["a"]["lines_of_business"] == ["property"]
