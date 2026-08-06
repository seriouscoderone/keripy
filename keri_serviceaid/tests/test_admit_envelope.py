"""The IPEX-admit path must produce the same envelope the watch path does — one
producer, two call sites (Amendment C §14.5)."""
from keri_serviceaid.envelope import envelope_for


def test_admit_emits_the_full_envelope_not_just_the_said():
    """Regression pin for the wiring. admit.py already had credential_said; what it
    lacked was credential_issuer and credential_edges, so a fold reading either got
    nothing and B22 hid the fact one event later."""
    acdc = {"v": "ACDC10JSON0001ae_", "d": "ECredSAID", "i": "EActuaryAid",
            "s": "ESchema",
            "e": {"d": "EEdge", "mandate": {"n": "EMandateSAID", "s": "ES", "o": "NI2I"}}}
    env = envelope_for(acdc, verified=True)
    assert env == {"credential_said": "ECredSAID",
                   "credential_issuer": "EActuaryAid",
                   "credential_edges": {"mandate": "EMandateSAID"}}


def test_admit_source_calls_the_shared_producer_rather_than_rebuilding_the_shape():
    """Structural, and deliberately so: two producers is the defect this task closes,
    and a copy-paste of the three keys into admit.py would pass every behavioural test
    while re-creating it."""
    import inspect

    from keri_serviceaid.providers import admit

    src = inspect.getsource(admit)
    assert "envelope_for" in src, "admit.py must call the shared producer"
    assert 'credential_edges"' not in src.replace("envelope_for", ""), (
        "admit.py must not construct envelope keys itself")
