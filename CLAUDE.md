# CLAUDE.md — keripy (seriouscoderone fork)

This is a **fork of `WebOfTrust/keripy`** (remote `fork` = `seriouscoderone/keripy`; `origin` =
WebOfTrust upstream). On top of stock keripy it adds a **serverless KERI stack**: `keri_cdk/` (a
distributable CDK library — witness / mailbox / core-table / Service-AID constructs), the
`ecosystems/keri_host/` app (the keri.host 5×5 witness+mailbox federation), `src/keri/db/dynamodbing.py`
(the `DynamoDBer` DynamoDB backend), and `src/keri/app/lambding.py` (serverless wiring). This file
captures conventions + gotchas that aren't obvious from the code. Keep it short and accurate.

## Push target

**Push to `fork` (seriouscoderone) ONLY — never `origin`/WebOfTrust.** Fork-only changes (the whole
serverless stack: `keri_cdk`, `dynamodbing`, `lambding`, Service-AID) diverge from upstream by design.

## Running tests

- Per-worktree venv. Install: `pip install -e . aws-cdk-lib constructs boto3 pytest pytest-asyncio moto`.
- **`moto` is required** for the `DynamoDBer` tests (`tests/db/test_dynamodbing*`) — without it the
  `dber` fixture **skips silently** (you'll see "passed" counts that omit the real coverage).
- `pytest-asyncio` for the mailbox SSE tests; `aws-cdk-lib`/`constructs` for the CDK synth tests
  (`tests/cdk/`). No `--import-mode` flag needed (that's a locksmith-only shadow workaround).

## CDK deploy (`ecosystems/keri_host/`)

- `cdk.json` runs `python app.py`, and **only the venv has `aws-cdk-lib`** — `source .venv/bin/activate`
  first so `python` resolves to it, then use **`npx aws-cdk@latest`** (the globally-installed `cdk`
  lags the lib schema).
- Build the arm64 runtime layer before deploy/synth: `keri_cdk/layers/build_layer.sh` (**needs Docker**;
  cross-builds in `public.ecr.aws/lambda/python:3.14-arm64`; output `keri_cdk/layers/keri_runtime/` is
  gitignored). Synth tests need that dir to *exist* (a placeholder is fine).
- The account must be CDK-bootstrapped. Operational AWS target so far: `AWS_PROFILE=personal`, us-east-1.
- Stack IDs are domain-derived (`Witness{slug}`/`Mailbox{slug}`); each node's namespace and keeper
  secret derive from the stack name.

## The shared-KEL "oracle" — read before touching `SHARED_KEL_STORES`

`lambding.py SHARED_KEL_STORES` is the set of stores the witness/mailbox/Service-AID nodes pool into a
single shared DynamoDB namespace (so a peer can read another AID's key state). It is **key-STATE +
reachability ONLY**: `kels. stts. ksns. knas. ends. locs. eans.`

**Do NOT add the per-witness receipt/event write-logs** (`wigs. rcts. evts. sigs. fels. fons. dtss.
wits. aess.`). Pooling them makes the N witnesses one writer to a shared `wigs.<said>` key
(last-writer-wins via `db.wigs.put`→`putIoSetVals`), so only one receipt survives and clients can't
reach `toad`-of-N — witnesses must each own their receipts for `agenting.Receiptor` to converge.
`tests/db/test_dynamodbing_namespace.py` guards this. (The 2026-06-18 SAM→CDK cutover hit this:
clients collected 1-of-5 until the set was narrowed.)

## KERI communication model — read before touching witnessing / receipts

**Field guide: `~/code/KERI-COMMUNICATION-MODEL.md`** (the canonical doc — TL;DR + 11 sections).
Load-bearing rules: a witness `/` event POST returns **`204`**; the receipt comes via the synchronous
**`/receipts`** endpoint (`agenting.Receiptor`) or a mailbox SSE poll — never on the event POST.
`agenting.WitnessReceiptor` encodes the direct-mode TCP push model and **hangs over HTTP/Lambda**.
Multi-witness `toad` convergence requires each witness to own its `db.wigs` (see the oracle rule above
and §6 of the field guide).

## `DynamoDBer` (`src/keri/db/dynamodbing.py`) gotchas

- **Consistency:** point reads by exact key are strongly consistent; GSI-served ordered/IoSet/range
  reads are **eventually consistent** (DynamoDB forbids `ConsistentRead` on a GSI). No false-accept
  risk (toad uses in-memory wigers), but expect transient stale reads; synchronous responders that
  gate on a GSI read can return a transient false-404.
- **IoSet/ordered appends** (`appendOnVal`, `addIoSetVal`, `putIoSetVals`) land at the first free
  ordinal via **conditional puts with retry** (`_append_at_free_ion`) — never revert to an
  unconditional put at a GSI-read max (it loses writes under concurrent writers / GSI lag).
- **Never run `clear=True` against a shared store** — it deletes the pooled data, not just one node's.
- **Changing a store's namespace routing (shared↔private) on a table with existing data is a
  MIGRATION**, not a config flip: existing KELs split across the old/new routing and fail to load.
  Greenfield fix = clear the `keri-core` table and let nodes re-incept (AIDs are deterministic from
  the keeper salt); do NOT clear a table while nodes are actively writing (races leave partial state).
- **Keeper:** one KMS-encrypted Secrets Manager secret per stack (`keri/<stack>/keeper`) holds the
  salt; the AID re-incepts deterministically from it. Force-deleting the secret → a fresh salt → a new
  AID.

## Design docs

`docs/superpowers/{specs,plans}/`. Recent: SAM→CDK federation cutover (`2026-06-18-sam-to-cdk-
federation-cutover*`), shared-KEL oracle (`2026-06-15-cdk-kel-oracle*` — **amended**; see its banner),
CDK Phase B/C.

## Infra note

The keri.host federation (5×5 witnesses + mailboxes), the shared-KEL oracle, and the Service-AID
framework live **here** (deployed via `keri_cdk`). The **Locksmith wallet** is a separate repo
(`~/code/locksmith`) that depends on **stock keripy APIs only** — keep wallet-facing changes
upstream-compatible.
