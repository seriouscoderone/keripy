# SAM → CDK Federation Cutover — Design

**Date:** 2026-06-18
**Status:** Approved (brainstorm) — ready for implementation plan
**Repo:** keripy fork (`~/code/keripy`), branch `feat/sam-to-cdk-cutover`
**Downstream dependents:** locksmith publisher **Task 9** (real publisher inception)

## Summary

Tear down the live SAM 5×5 KERI federation and stand up a fresh CDK 5×5 federation in its
place, across the 5 existing domains, reusing the same public subdomain names. The federation is
**greenfield**: it is not real production, nothing depends on it, and there is **no migration** —
the old trust roots (witness/mailbox AIDs) are abandoned, and the fresh deploy mints all-new
salts → AIDs → OOBIs.

The witness/mailbox CDK constructs are already federation-ready (each `WitnessStack` /
`MailboxStack` takes `domain_name` + `hosted_zone_id` + `*_url` and wires its own ACM cert,
API-Gateway custom domain, Route53 record, keeper secret, and LeadingKeys policy). The app today
hard-codes a single witness+mailbox pair. So the deploy-side work is mostly **looping the app over
5 domains**; the heavy lift is the **teardown choreography** and the **validation gate**.

### Locked decisions (from brainstorm)

1. **Sequencing — teardown-first (big-bang).** Destroy the entire SAM federation first, then
   deploy the fresh CDK 5×5 **reusing the same subdomain names** (`witness.keri.host`,
   `mailbox.keri.host`, …). No naming/DNS/cert collisions, clean slate. A brief window with no
   live federation is acceptable (nothing depends on it).
2. **Oracle — ON (keep current wiring).** The witness/mailbox handlers already pass
   `shared_namespace="shared"` + `SHARED_KEL_STORES`. All 5 witnesses + 5 mailboxes pool their
   public KEL stores into the `shared#` namespace on the one `keri-core` table; private state and
   each `Reger` stay per-service-namespaced. For a federation this is stronger than for the single
   validation pair — every node sees the federation's collective first-seen view — and it exercises
   the oracle ahead of the first Service-AID consumer.
3. **Validation depth — probes + throwaway-client e2e.** The real publisher inception +
   persisted anchor + verify-gate activation stays as the **downstream Task 9** (separate spec,
   spanning locksmith). This spec ends at a throwaway 3-of-5 client provably working against the
   fresh federation.

### Environment / guardrails

- All AWS operations target `AWS_PROFILE=personal`, `us-east-1`, account `117870855864`.
- Git push target is **`fork` (seriouscoderone) only** — never `origin`/WebOfTrust.
- The 5 Route53 **hosted zones are preserved** (the fresh deploy reuses them). Only
  stack-owned resources (certs, A-records, ACM-validation CNAMEs) are destroyed.
- Teardown is **destructive and irreversible** against real cloud resources — the implementation
  plan must gate it behind an explicit confirmation and a discover-before-destroy step.

## Current state (verified)

- **keripy tip:** `59fec178` on `development` (forked to `feat/sam-to-cdk-cutover`).
- **App:** `ecosystems/keri_host/app.py` instantiates exactly `KeriCoreStack` + 1 `WitnessStack`
  + 1 `MailboxStack`, all domain/zone values from CDK context (`-c witness_domain=…`,
  `-c hosted_zone_id=…`). No multi-domain scaffolding exists; the 5 federation domains appear only
  in placeholder comments and probe defaults.
- **Oracle:** wired ON in `keri_cdk/handlers/{witness,mailbox}/*_handler.py`
  (`shared_namespace="shared", shared_stores=SHARED_KEL_STORES`); `SHARED_KEL_STORES` defined in
  `src/keri/app/lambding.py`.
- **Stacks are federation-ready:** `WitnessStack`/`MailboxStack` constructors already accept
  `domain_name`, `hosted_zone_id`, `*_url`, `core_table`, and derive namespace (`{Aws.STACK_NAME}:kel`
  / `:mbx`) and keeper secret (`keri/{Aws.STACK_NAME}/keeper`) from the stack name. CWD-relative
  asset-path bug already fixed. **No construct changes required.**
- **Destroy target (re-verify at teardown):** 20 SAM CloudFormation stacks (5 witness + 5 mailbox
  + 10 SAM companion stacks), 20 DynamoDB tables (per-service `{witness,mailbox}-*-{db,ks}`,
  including the old plaintext `-ks` keeper tables), 10 custom domains → 10 ACM certs + Route53
  records across the 5 hosted zones (keri.host / honest.town / verdadero.me / goonei.com /
  legitim.us).

## Architecture — units

The work decomposes into 6 well-bounded units. Units 1–5 are keripy; Unit 6 is locksmith
(optional, cross-repo).

### Unit 1 — Federation config (privacy-preserving)

The 5 `(domain, hosted_zone_id)` pairs must not be committed (zone IDs are private). Reuse the
three-tier injection convention already proven in locksmith (`deploy_config.json`):

- `ecosystems/keri_host/federation.json` — **gitignored**, the real 5-domain list.
- `ecosystems/keri_host/federation.example.json` — **committed**, `example.com` placeholders.
- Resolution order in `app.py`: `$KERI_HOST_FEDERATION` env var (path or inline JSON) →
  gitignored `federation.json` → committed `federation.example.json`.

Shape:

```json
[
  { "slug": "KeriHost",    "domain": "keri.host",    "hosted_zone_id": "Z..." },
  { "slug": "HonestTown",  "domain": "honest.town",  "hosted_zone_id": "Z..." },
  { "slug": "VerdaderoMe", "domain": "verdadero.me", "hosted_zone_id": "Z..." },
  { "slug": "GooneiCom",   "domain": "goonei.com",   "hosted_zone_id": "Z..." },
  { "slug": "LegitimUs",   "domain": "legitim.us",   "hosted_zone_id": "Z..." }
]
```

Add `federation.json` to `.gitignore`. The example file's placeholder zone IDs and `example.com`
domains keep the public repo clean.

### Unit 2 — App generalization (1×1 → 5×5)

`ecosystems/keri_host/app.py` loads the config and loops: one `KeriCoreStack` + 5 `WitnessStack`
+ 5 `MailboxStack`.

- **Stack IDs are domain-derived, not index-derived:** `Witness{slug}` / `Mailbox{slug}` (e.g.
  `WitnessKeriHost`, `MailboxHonestTown`). Rationale: the per-stack `<stack>:kel` namespace and
  `keri/<stack>/keeper` secret are derived from the stack name; index-based names would silently
  re-map every node's namespace and keeper secret if the config list were reordered. Domain-derived
  names are stable per domain.
- `domain_name = f"witness.{domain}"` / `f"mailbox.{domain}"`; `*_url = f"https://{domain_name}"`;
  `hosted_zone_id` from config; `core_table = core.table` for all.
- `alias` per stack = its slug (drives the LeadingKeys scope where applicable).
- Each witness/mailbox mints its own fresh salt → AID on first cold start (oracle-on wiring
  unchanged).
- **CDK synth tests** (`tests/cdk/test_keri_host_app.py`) updated to assert the 5×5 shape: 11
  stacks (1 core + 5 witness + 5 mailbox), per-stack LeadingKeys four-pattern union (incl. the
  `shared#*` / `__meta__#shared#*` oracle grants), correct namespaces, and 10 distinct keeper-secret
  paths. Per-stack tests (`test_witness_stack.py` / `test_mailbox_stack.py`) unchanged (constructs
  unchanged).

### Unit 3 — SAM teardown (discover → destroy → verify-zero-trace)

A re-runnable runbook/script (`ecosystems/keri_host/teardown_sam.{sh,py}` — exact form decided in
the plan), **not** a blind `delete-stack` loop.

- **Discover first.** Enumerate the actual live resources (CloudFormation `serverless-*` stacks,
  DynamoDB tables, ACM certs, stack-owned Route53 records) and print an inventory. Verify reality
  matches this design's destroy target before destroying anything (look at the target before
  deleting it).
- **Destroy, encoding the known gotchas** (from the Phase C + oracle teardowns, now at ×10 scale
  across 5 zones):
  - Disable DynamoDB `DeletionProtection` (`update-table --no-deletion-protection-enabled`) and
    stack `TerminationProtection` (`update-termination-protection`) before delete.
  - ACM certs that hit `DELETE_FAILED` (in-use lag after the API-GW custom domain is removed):
    `delete-stack --retain-resources <CertLogicalId>`, then manual `acm delete-certificate` once
    the domain is gone.
  - Keeper-related secrets: `--force-delete-without-recovery`.
  - Sweep leftover Route53 ACM-validation CNAMEs and A-records that linger after stack deletion.
- **Verify zero-trace.** No `serverless-*` stacks, no old per-service tables, no old certs, no
  orphaned DNS records. The 5 hosted zones remain (and are now empty of federation records, ready
  for the fresh deploy).
- **Idempotent / re-runnable:** safe to run repeatedly; each step checks existence before acting.

### Unit 4 — Deploy

- Build the arm64 runtime layer: `keri_cdk/layers/build_layer.sh` (produces the gitignored
  `keri_cdk/layers/keri_runtime/`).
- Deploy from `ecosystems/keri_host/`: `npx aws-cdk@latest deploy --all` (CLI/lib version skew —
  use `npx …@latest`, as in Phase C). Core deploys first (Witness/Mailbox import its CoreTable
  export — cross-stack lifecycle lock).
- **Post-deploy AID harvest.** Resolve the 5 fresh witness AIDs and 5 fresh mailbox AIDs from their
  OOBI/controller endpoints (`https://witness.<domain>/oobi`, etc.) into a deploy artifact
  (gitignored, e.g. `ecosystems/keri_host/federation_aids.json`). A client needs `(AID, URL)` pairs
  to incept against the witnesses and configure the mailbox. This artifact is the hand-off to Task 9.

### Unit 5 — Validation gate

Run in order; each gate must pass before the next:

1. **CDK synth tests** — `pytest tests/cdk/ -v` (the 5×5 assertions from Unit 2).
2. **Conformance probes** — `keri_cdk/probes/witness_conformance` and `mailbox_conformance`
   against all 10 live endpoints (`WITNESS_URL=https://witness.<domain> python probe.py`, ×5 each).
3. **LeadingKeys probe (16/16)** — `keri_cdk/probes/leadingkeys/probe.py` against the real
   `keri-core` table: cross-tenant private read/GSI/write all DENY; shared read/GSI/write/meta all
   ALLOW.
4. **Oracle-pooling check** — confirm `shared#kels.` holds KELs from multiple distinct nodes (the
   federation's collective first-seen view) and that private stores stay under each `<stack>:kel` /
   `:mbx` namespace.
5. **Throwaway-client e2e** — a real-keripy client AID incepts against the 5 witnesses at **toad
   3-of-5**, collects receipts via `agenting.Receiptor` (never `WitnessReceiptor` — it hangs over
   HTTP), round-trips a mailbox message through one of the 5 mailboxes, then the throwaway AID is
   torn down. This proves a real multi-witness wallet client works against the fresh federation.

### Unit 6 — #158 privacy scrub (optional, cross-repo — locksmith)

Small and separable from the keripy cutover: scrub `releases.keri.host` from locksmith's
`.github/workflows/release.ci.yml` + `infrastructure/README.md` (the federation domains were
already scrubbed in a prior session; the CDN domain + CI config remain). Flagged as a final task;
easy to split into its own change if preferred.

## Data flow

```
federation.json (5 domains + zone IDs, gitignored)
        │
        ▼
app.py loop ──▶ KeriCoreStack (keri-core table, RETAIN, DeletionProtection, PITR)
        │            ▲
        ├──▶ 5× WitnessStack  ─┤ import CoreTable; namespace <stack>:kel + shared#; keeper keri/<stack>/keeper
        └──▶ 5× MailboxStack  ─┘ import CoreTable; namespace <stack>:mbx + shared#; keeper keri/<stack>/keeper
                     │
   (first cold start)│ each mints fresh salt → AID; witnesses/mailboxes self-incept
                     ▼
        post-deploy AID harvest ──▶ federation_aids.json (gitignored hand-off to Task 9)
                     │
                     ▼
        validation: synth → conformance ×10 → LeadingKeys 16/16 → oracle pooling → 3-of-5 client e2e
```

## Error handling / risks

- **Teardown partial failure** (the dominant risk): the runbook is idempotent and verifies
  zero-trace, so a re-run completes a partially-failed teardown. ACM/DNS lag is expected and
  handled explicitly (retain-resources + manual delete; CNAME sweep).
- **Deploy ordering:** Core must deploy before (and be deleted after) the witness/mailbox stacks
  (cross-stack `CoreTable` export). `cdk deploy --all` respects this; teardown of a *future* CDK
  redeploy must delete Core last.
- **Operational invariant (oracle):** never run `clear=True` against a shared store — it would
  delete the pooled KEL, not just one node's. No code path does this; documented as a standing
  guard.
- **CLI/lib skew:** deploy via `npx aws-cdk@latest` (global cdk lags the lib schema).
- **Worktree venv caveat:** the build worktree's venv may need `pytest-asyncio` and a placeholder
  `keri_cdk/layers/keri_runtime/` dir for synth tests (the real layer is built by `build_layer.sh`).

## Out of scope

- **Publisher Task 9** — real publisher inception (fresh AID, 3-of-5 over the new witnesses),
  persisting the anchor to `src/locksmith/release/publisher_anchor.json`, and activating the
  locksmith verify gate. Separate downstream spec; this cutover is its prerequisite. The
  `federation_aids.json` artifact from Unit 4 is the hand-off.
- **WatcherStack** — remains a `NotImplementedError` seam; not part of the federation.
- **Service-AID deploy** — the framework is merged but no Service-AID is deployed in this cutover;
  the oracle is dogfooded by the witness/mailbox federation itself.

## Definition of done

Zero SAM trace (no `serverless-*` stacks, old tables, or old certs; 5 zones preserved) **and** 10
healthy CDK endpoints on the original subdomain names **and** all validation gates green (synth,
conformance ×10, LeadingKeys 16/16, oracle pooling, throwaway 3-of-5 client e2e) **and** the
`federation_aids.json` hand-off produced. Task 9 is unblocked.
