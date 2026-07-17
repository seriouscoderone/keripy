"""TDD for `frame_grant_for`/`self_issue_and_grant`'s `return_raw` kwarg
(Task B0, sub-project B's opening agenda item).

`frame_grant_for` framed the IPEX grant exn, parsed it locally, and returned
only the SAID -- callers needing the raw bytes for peer-aware delivery
(Locksmith's upcoming ServiceaidGrantDoer) had no path but the private
`_frame_grant` helper (see `test_admit.py`'s `delivered_grant` fixture,
rewired by this task onto the public `return_raw=True` shape instead).

This module proves the additive kwarg: the default shape is byte-identical
to before, `return_raw=True` adds the raw bytes without changing anything
else, and -- the load-bearing case -- those raw bytes are the actual
deliverable framed message: parseable by a SECOND, independent Habery's own
Exchanger (as a real mailbox delivery would feed it) into the SAME grant
SAID, not merely some other artifact that happens to compute the same
digest.
"""
from keri.app.habbing import Habery
from keri.core import parsing, serdering
from keri.core.signing import Salter
from keri.kering import Vrsn_1_0
from keri.peer import exchanging
from keri.vdr import credentialing

from keri_serviceaid.providers.issue import (
    frame_grant_for, issue_credential, self_issue_and_grant,
)


def _introduce_kel(dst_hby, src_hab):
    """Mocked OOBI resolution: feed src_hab's full KEL into dst_hby's own
    Kevery so dst recognizes src as a known counterparty (mirrors
    test_admit.py's helper of the same name / conftest.py's `recipient_pre`
    fixture)."""
    parsing.Parser(kvy=dst_hby.kvy, version=Vrsn_1_0).parse(
        ims=bytearray(src_hab.replay()))
    dst_hby.kvy.processEscrows()


def test_frame_grant_for_default_returns_said_only(
        issuer_hby, rating_schema, recipient_pre):
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-default")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-default", temp=True)
    credential_said = issue_credential(
        issuer_hby, hab, rgy, schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 1}, registry_name="svc-default")

    result = frame_grant_for(issuer_hby, hab, rgy,
                             credential_said=credential_said, recipient=recipient_pre)

    assert isinstance(result, str)
    assert result.startswith("E")


def test_frame_grant_for_return_raw_true_returns_said_and_bytes(
        issuer_hby, rating_schema, recipient_pre):
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-raw")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-raw", temp=True)
    credential_said = issue_credential(
        issuer_hby, hab, rgy, schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 1}, registry_name="svc-raw")

    result = frame_grant_for(issuer_hby, hab, rgy,
                             credential_said=credential_said, recipient=recipient_pre,
                             return_raw=True)

    assert isinstance(result, tuple) and len(result) == 2
    grant_said, raw = result
    assert isinstance(grant_said, str) and grant_said.startswith("E")
    assert isinstance(raw, bytes) and len(raw) > 0
    # Re-deriving the SAID from the raw bytes independently must match the
    # SAID handed back alongside them -- they describe the same message.
    assert serdering.SerderKERI(raw=raw).said == grant_said


def test_self_issue_and_grant_default_returns_2_tuple(
        issuer_hby, rating_schema, recipient_pre):
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-sig-default")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-sig-default", temp=True)

    result = self_issue_and_grant(
        issuer_hby, hab, rgy, schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 2}, registry_name="svc-sig-default")

    assert isinstance(result, tuple) and len(result) == 2
    credential_said, grant_said = result
    assert isinstance(credential_said, str) and credential_said.startswith("E")
    assert isinstance(grant_said, str) and grant_said.startswith("E")


def test_self_issue_and_grant_return_raw_true_returns_3_tuple(
        issuer_hby, rating_schema, recipient_pre):
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-sig-raw")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-sig-raw", temp=True)

    result = self_issue_and_grant(
        issuer_hby, hab, rgy, schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 3}, registry_name="svc-sig-raw", return_raw=True)

    assert isinstance(result, tuple) and len(result) == 3
    credential_said, grant_said, raw = result
    assert isinstance(credential_said, str) and credential_said.startswith("E")
    assert isinstance(grant_said, str) and grant_said.startswith("E")
    assert isinstance(raw, bytes) and len(raw) > 0
    assert serdering.SerderKERI(raw=raw).said == grant_said


def test_frame_grant_for_threads_message_into_exn(
        issuer_hby, rating_schema, recipient_pre):
    """Regression (Task B9 fix): `frame_grant_for` used to hardcode
    `message=""` when calling `_frame_grant`, silently dropping any
    user-typed IPEX message routed through the serviceaid bridge (the
    legacy Locksmith `SendGrantDoer` path preserved it). The exn's human
    message lives at `sad["a"]["m"]` -- see `specialExchange`'s `attributes`
    handling (`peer/exchanging.py`) and `ipexGrantExn`'s `data = dict(m=message, ...)`
    (`vc/protocoling.py`)."""
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-message")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-message", temp=True)
    credential_said = issue_credential(
        issuer_hby, hab, rgy, schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 1}, registry_name="svc-message")

    grant_said, raw = frame_grant_for(
        issuer_hby, hab, rgy, credential_said=credential_said,
        recipient=recipient_pre, message="please review", return_raw=True)

    serder = serdering.SerderKERI(raw=raw)
    assert serder.said == grant_said
    assert serder.sad["a"]["m"] == "please review"


def test_frame_grant_for_default_message_is_empty_string(
        issuer_hby, rating_schema, recipient_pre):
    """Byte-identical-to-before default: omitting `message` still frames an
    empty-string `m`, matching pre-fix behavior for every existing caller."""
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-message-default")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-message-default", temp=True)
    credential_said = issue_credential(
        issuer_hby, hab, rgy, schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 1}, registry_name="svc-message-default")

    grant_said, raw = frame_grant_for(
        issuer_hby, hab, rgy, credential_said=credential_said,
        recipient=recipient_pre, return_raw=True)

    serder = serdering.SerderKERI(raw=raw)
    assert serder.sad["a"]["m"] == ""


def test_return_raw_bytes_parse_into_second_habery_exchanger(
        issuer_hby, rating_schema, recipient_pre):
    """Load-bearing: the raw bytes returned under `return_raw=True` are the
    ACTUAL deliverable framed message -- not a re-serialization that merely
    reproduces the same digest. Proof: hand them to a SEPARATE, independent
    Habery's own Exchanger (as a real mailbox delivery would) and confirm it
    recovers the SAME grant SAID via `exchanging.cloneMessage`. A
    re-serialization would still need to carry the exact original signature
    attachments to pass `exchanging`'s signature verification here -- so a
    successful cross-party parse proves these are the wire bytes, not a
    reconstruction.
    """
    schema_said, _sad = rating_schema
    hab = issuer_hby.makeHab(name="svc-crossparty")
    rgy = credentialing.Regery(hby=issuer_hby, name="svc-crossparty", temp=True)

    credential_said, grant_said, raw = self_issue_and_grant(
        issuer_hby, hab, rgy, schema_said=schema_said, recipient=recipient_pre,
        attributes={"score": 4}, registry_name="svc-crossparty", return_raw=True)

    # A second, independent Habery -- the "recipient mailbox" -- must know
    # the granter's KEL before it can verify the exn's signature (mirrors
    # conftest.py's `recipient_pre` / test_admit.py's `_introduce_kel`).
    receiver_hby = Habery(name="rcv", temp=True,
                          salt=Salter(raw=b'0receiver-salt01').qb64)
    try:
        _introduce_kel(receiver_hby, hab)
        exc = exchanging.Exchanger(hby=receiver_hby, handlers=[])

        parsing.Parser().parseOne(ims=bytes(raw), exc=exc, version=Vrsn_1_0)

        serder, pathed = exchanging.cloneMessage(receiver_hby, grant_said)
        assert serder is not None
        assert serder.said == grant_said
    finally:
        receiver_hby.close()
