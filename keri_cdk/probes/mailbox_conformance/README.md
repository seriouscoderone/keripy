# mailbox_conformance — live mailbox endpoint conformance probe

End-to-end conformance checks that exercise a **deployed** Lambda mailbox as a
third-party KERI controller would.  The probe targets the **serverless
notify-and-fetch** flow introduced in Phase 3 (§5.3 / §5.5 / §5.7).

## Serverless flow

```
(a) WS subscribe  — open wss:// (URL from GET / status JSON "ws" field);
                    send {"action":"subscribe","qry":"<base64 signed /mbx qry>"}
(b) Deposit       — POST /fwd over REST to deliver a message for the recipient
(c) Nudge         — assert {"type":"mailbox.nudge","pre":"...","topic":"..."} frame
                    arrives on the WS within a bounded timeout
(d) Drain-and-close — POST signed qry r=/mbx; response delivers the backlog
                    and CLOSES (drain-then-EOF, NOT a long-poll stream);
                    assert X-Mailbox-Mode: notify-and-fetch header is present
```

The old "stream stays open" assertion (long-poll `text/event-stream` held for
780 seconds) has been replaced by the drain-then-EOF check.  A timed-out
`_drain_mbx_response` call would fail the test, proving the server actually
closes the connection rather than holding it open.

## Tests

| Test | Safe against production? | Notes |
|---|---|---|
| `test_status_endpoint_returns_mailbox_metadata` | Yes | Now also asserts `mode` + `ws` fields |
| `test_oobi_returns_signed_cesr_stream` | Yes | |
| `test_oobi_advertises_mailbox_role` | Yes | |
| `test_oobi_does_not_advertise_witness_role` | Yes | |
| `test_post_empty_body_returns_400` | Yes | |
| `test_kel_post_returns_204` | Yes | |
| `test_mbx_query_returns_onboarding_headers` | Yes (read-only drain) | Asserts `X-Mailbox-Mode` + `X-Mailbox-Client` headers |
| `test_mbx_query_missing_q_pre_returns_400` | Yes | |
| `test_ws_subscribe_then_deposit_nudge_drain` | **Dev stage only** | Full subscribe→deposit→nudge→drain round-trip |

Do **NOT** run `test_ws_subscribe_then_deposit_nudge_drain` against the live
`mailbox.keri.host` production mailbox without explicit confirmation.  Task 8
runs the full suite against a dev stage.

## Run

```bash
# Full suite against a dev stage:
MAILBOX_URL=https://dev.mailbox.keri.host pytest keri_cdk/probes/mailbox_conformance/probe.py -v

# Local sam local / moto stack:
MAILBOX_URL=http://localhost:3000 pytest keri_cdk/probes/mailbox_conformance/probe.py -v

# Safe subset only (no deposit/WS) — can target production:
pytest keri_cdk/probes/mailbox_conformance/probe.py -v \
  -k "not deposit_nudge_drain"
```

Default target is `https://mailbox.keri.host`.

This is a real-network diagnostic, not a unit test — it lives under
`keri_cdk/probes/` (alongside `layer_smoke`, `leadingkeys`, `gsi-staleness`,
`concurrent-append`) and is **not** part of the `pytest tests/` suite.

## Deps

```bash
.venv/bin/pip install websockets
```
