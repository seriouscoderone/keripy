# Service-AID Framework — Follow-Ups

Status: v1 foundation slice **SHIPPED** to `development` (merge `59fec178`, branch `feat/service-aid-runtime`, 2026-06-17). 56 tests green; final whole-branch review = ready-to-merge.

Source of truth for design follow-ons: the spec's "Out of scope (named follow-ons)" section in `docs/superpowers/specs/2026-06-17-service-aid-framework-design.md`. This file adds the **operational** next-steps that came out of the build itself (not in the spec), and a reconciliation note.

---

## A. Operational next-steps (from the build; not in the spec)

1. **First real deploy — NEVER RUN YET.** `examples/gated_retrieval/DEPLOY_RUNBOOK.md` is the operator-run validation that synth tests can't do: build both arm64 layers, `cdk deploy`, confirm witnessed inception via `Receiptor` (not a `WitnessReceiptor` hang), POST a signed exn → oracle verify → grant delivered to `mailbox.keri.host`, requester polls SSE + admits, exercise ≥2 routes, replay (idempotent re-deliver), then `cdk destroy`. This is the highest-value next action — it validates the layer-resident-handler + libsodium real-deploy unknown.

2. **`keri_serviceaid` packaging metadata.** The package has no `pyproject.toml`/`setup.py`, so `build_framework_layer.sh`'s `pip install --no-deps ./keri_serviceaid` always fails and the `cp -R` fallback is the live install path. Add minimal packaging before publishing `ServiceAidFrameworkLayer` as a reusable artifact. (The `2>/dev/null` on the pip step could also mask a different failure someday.)

3. **Witnessed-TEL issuance completion.** v1 deploys `witnesses=[]` + a `noBackers=True` registry, so AID inception can be witnessed (Receiptor) while TEL issuance stays unwitnessed — the plan-sanctioned fallback. Completing witnessed TEL issuance (the `tpwe` escrow convergence problem documented in `keri_serviceaid/providers/issue.py` module docstring) is deferred; needed if a real service must issue from a witnessed registry.

## B. Design follow-ons (named in the spec — restated for convenience)

4. **`CredentialGate` authz** — the crown jewel. Presented-ACDC verification via Tevery extraction + `required_schema` (the "prove-then-retrieve" gate). Seam exists: `Authorizer` Protocol + the `Allowlist` docstring naming it; `Request.credentials` is `[]` under Allowlist today.
5. **Watcher tier-3 verification** — `OracleVerifier(tier="watcher")` currently raises `NotImplementedError`. The `keri_cdk` watcher seam.
6. **Signed denials** — replace v1 silence on deny/reject with a signed spurn (`/ipex/spurn`) / denial-note exn to the requester's mailbox.
7. **DLQ / EventBridge auto-retry** — so the client need not re-send on a delivery failure (v1 is client-retry + idempotent re-deliver).
8. **Mailbox-inbound option** — Service-AID drains its own `mailbox.keri.host` on a schedule (v1 uses a direct CESR-ingest endpoint).
9. **Micro-app template loader / UEL / aggregates / projections** — explicitly out of this effort.

## C. RECONCILE before building reachability-dependent follow-ons

`development` advanced past the Service-AID merge with commits that may interact:
- **`b96fe61f`** — amended the 2026-06-15 oracle spec: "narrowed to key-state (write-logs broke receipts)." Task 7 added `ends./locs./eans.` to `SHARED_KEL_STORES`. **Confirm the reachability-store sharing still squares with the narrowed oracle scope** (and that receipts aren't broken by it) before relying on `OracleResolver` for in-domain reachability.
- **`feat/witness-ddb-first-seen`** (`2ee629b3`) — replaces witness `reserved_concurrency=1` with a DynamoDB conditional-write first-seen. `ServiceAidFunction` still sets `reserved_concurrent_executions=1` for single-writer safety; check whether the conditional-write approach should also apply to the Service-AID Function (would relax the concurrency=1 constraint).
- **`CLAUDE.md`** (`5ea9d6ec`) — new fork conventions doc on `development`; worth reading at the start of any follow-on.

## D. Minor hardening notes (non-blocking, from the whole-branch review)

- `OracleResolver.resolve` now guards the one-hab-per-process assumption (raises `LookupError` on 0 or >1 habs) — already applied (`f5fd3d72`).
- The inception module deploy-layout contract (`_inception` must be importable from the asset) is noted in the runbook prereqs — already applied (`f5fd3d72`).
