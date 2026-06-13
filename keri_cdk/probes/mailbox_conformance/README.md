# mailbox_conformance — live mailbox endpoint conformance probe

End-to-end conformance checks that exercise a **deployed** Lambda mailbox as a
third-party KERI controller would: status endpoint, OOBI signed-CESR stream,
role-advertisement correctness (mailbox role yes, witness role no), KEL ingest
(`204`), `/mbx` streaming query, deposit-then-poll round-trip, and `400` error
paths.

This is a real-network diagnostic, not a unit test — it lives under
`keri_cdk/probes/` (alongside `layer_smoke`, `leadingkeys`, `gsi-staleness`,
`concurrent-append`) and is **not** part of the `pytest tests/` suite. It needs a
reachable mailbox; with no endpoint it will error rather than skip.

## Run

```bash
pytest keri_cdk/probes/mailbox_conformance/probe.py -v
# point at a local `sam local` / dev deployment:
MAILBOX_URL=http://localhost:3000 pytest keri_cdk/probes/mailbox_conformance/probe.py -v
```

Default target is `https://mailbox.keri.host`; override with `MAILBOX_URL`.
