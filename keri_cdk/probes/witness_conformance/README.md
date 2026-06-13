# witness_conformance — live witness endpoint conformance probe

End-to-end conformance checks that exercise a **deployed** Lambda witness as a
third-party KERI controller would: status endpoint, OOBI signed-CESR round-trip,
receipting (`POST /receipts`, `POST /`, KLI-format, attachment-group wrapper),
404/400 error paths, and role-advertisement correctness (witness role yes,
mailbox role no).

This is a real-network diagnostic, not a unit test — it lives under
`keri_cdk/probes/` (alongside `layer_smoke`, `leadingkeys`, `gsi-staleness`,
`concurrent-append`) and is **not** part of the `pytest tests/` suite. It needs a
reachable witness; with no endpoint it will error rather than skip.

## Run

```bash
pytest keri_cdk/probes/witness_conformance/probe.py -v
# point at a local `sam local` / dev deployment:
WITNESS_URL=http://localhost:3000 pytest keri_cdk/probes/witness_conformance/probe.py -v
```

Default target is `https://witness.keri.host`; override with `WITNESS_URL`.
