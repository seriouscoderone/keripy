"""The envelope-provenance producer — the one thing that turns a verified ACDC
into the event shape every micro-app fold is written against."""
import pytest

from keri_serviceaid.envelope import EnvelopeError, envelope_for

MANDATE = {
    "v": "ACDC10JSON0001ae_",
    "d": "EMandateSAIDMandateSAIDMandateSAIDMandateSA",
    "i": "ECuoAidCuoAidCuoAidCuoAidCuoAidCuoAidCuoAid",
    "ri": "ERegistrySAIDRegistrySAIDRegistrySAIDRegist",
    "s": "ESchemaSAIDSchemaSAIDSchemaSAIDSchemaSAIDSc",
    "a": {"d": "EAttrBlockSAID", "line_of_business": "auto", "jurisdiction": "US-UT"},
}

ATTESTATION = dict(
    MANDATE,
    d="EAttestSAIDAttestSAIDAttestSAIDAttestSAIDAt",
    e={"d": "EEdgeBlockSAID", "mandate": {"n": MANDATE["d"], "s": MANDATE["s"], "o": "NI2I"}},
)


def test_credential_said_and_issuer_come_off_the_acdc():
    env = envelope_for(MANDATE, verified=True)
    assert env["credential_said"] == MANDATE["d"]
    assert env["credential_issuer"] == MANDATE["i"]


def test_edges_are_resolved_by_name_to_their_far_node_said():
    """`credential_edges.<name>` is the far node's SAID — what the template reads
    as `event.credential_edges.mandate`. The `d` of the edge BLOCK is not an edge."""
    env = envelope_for(ATTESTATION, verified=True)
    assert env["credential_edges"] == {"mandate": MANDATE["d"]}


def test_an_acdc_with_no_edge_block_gets_an_empty_edge_map_not_a_missing_key():
    """A fold reading `event.credential_edges` must never see an absent key: in CEL
    that is an error, and register finding B22 shows a CEL error inside a map literal
    is RETURNED as state rather than raised, so it would be misattributed one event later."""
    env = envelope_for(MANDATE, verified=True)
    assert env["credential_edges"] == {}


def test_an_unverified_acdc_produces_NO_envelope_at_all():
    """B22 is why this refuses rather than degrades. A partial envelope reaches the
    fold as a map literal whose bad value becomes state; nothing raises. The producer
    is the last place a refusal is still cheap."""
    with pytest.raises(EnvelopeError, match="not verified"):
        envelope_for(MANDATE, verified=False)


@pytest.mark.parametrize("missing", ["d", "i"])
def test_a_missing_required_field_refuses_rather_than_emitting_a_partial(missing):
    acdc = {k: v for k, v in MANDATE.items() if k != missing}
    with pytest.raises(EnvelopeError, match=missing):
        envelope_for(acdc, verified=True)


def test_the_envelope_carries_no_key_outside_RESERVED_ENVELOPE():
    """The loader's allowlist is the contract. A producer inventing a sixth name
    would be read by nothing and mask the absence of a name that IS read."""
    env = envelope_for(ATTESTATION, verified=True)
    assert set(env) == {"credential_said", "credential_issuer", "credential_edges"}
