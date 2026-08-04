# Fork divergence from WebOfTrust/keripy

Upstream does not accept AI-authored contributions, so these changes are
permanent. Everything described below must survive a future upstream merge.

**This document is a guide, not the checklist.** It is written by hand and it
goes stale. The authoritative, always-current list of what diverges is:

```
git diff --stat $(git merge-base origin/main development)..development
```

Run that first, and reconcile anything it shows that is not described here.
The prose exists to explain *why* an area diverges and what breaks if it is
dropped — it cannot enumerate every hunk, and a merger who treats it as
complete will drop something. The first table covers only the
log-triggered-retrieval work; § Areas that diverge sketches the rest.

## Log-triggered retrieval (`pro`/`bar`, anchoring, sealing)

| Change | Commit | Why | What breaks if dropped |
|---|---|---|---|
| `Baser.fetchLastSealingEventBySeal` converts the caller's seal dict before comparing | `d09ca318` | It compared a `dict` to a `namedtuple`, so it returned `None` for **every** seal shape. Its two siblings do convert. | Anchor lookup by digest; `AnchorQuerier` hangs; `AnchorWatcher` and the whole trigger path |
| `AnchorQuerier` completion calls the general seal finder | `d09ca318` | It called the `SealEvent`-only finder, so it never terminated on a `SealDigest` anchor | Waiting for a digest anchor never returns |
| `parsing.py` dispatches `Ilks.pro` / `Ilks.bar` | `51a5deef` | There was no branch at all — a signed `pro` died at `msgProcess`'s fall-through `else` with `Unexpected message ilk`, so the handlers were unreachable from the wire | All prod/bare retrieval |
| `Kevery.processPro` / `processBar` implemented | `51a5deef` | Both were `pass` stubs | All prod/bare retrieval |
| `keri/app/prodding.py` — default-deny disclosure responder | `bb7fda62`, `a742dc96` | Nothing answered a prod. The policy takes the prod itself so it can gate on `q["az"]`; a raising policy fails closed and does not stall the cue drain | Retrieval, and the disclosure-policy safety properties |
| `keri/app/anchoring.py` — `AnchorWatcher` | this plan | The queriers only wait for *known* anchors; nothing reports new ones | Log-triggered micro-apps |
| `keri/core/sealing.py` — `verifySealedBody` | this plan | The trust property for log-triggered retrieval. Re-derivation is dispatched on the SAD's version string: `SerderACDC` for an ACDC, `SerderKERI` for a KERI message, `Saider.saidify` only for flat unversioned SADs, plain digest for opaque blobs. The body's own `d` is checked against the seal, because `Saider._derive` dummies `d` and so never verifies it | Bodies would be trusted by sender rather than by re-derivation; a saidify-only verifier rejects **every real v2 ACDC** (measured), and without the `d` check a body carries an attacker-chosen identity while verifying True |
| `keri/app/prodding.py` — `ProdClient` | this plan | Nothing in `src/` ever *sent* a `pro`. Defaults `pvrsn=Vrsn_1_0` to match `ProdResponder` | Retrieval requests; a version mismatch yields **silence**, not an error |
| `keri/vc/proving.py` — `credential()` picks the registry field by version | this fix | It wrote `vc["ri"] = status` unconditionally. ACDC **v2 renamed `ri` → `rd`** and the v2 top-level field domain is `strict=True`, so **every** registry-backed v2 credential raised `SerializeError: Unallowed extra field(s) = ['ri']` — including the default call path, because `credential()`'s own `version` default resolves to v2. The vendored ACDC v1.1 spec uses `rd` throughout and mentions `ri` **zero times**. Verified present on upstream `main` @ `f4b9e3e8` too, so this is an upstream gap, not fork drift | Any v2 registry-backed ACDC. Callers had to know to pass `version=Vrsn_1_0` |
| `keri/cli/commands/ipex/admit.py` — TEL query reads `ri` **or** `rd` | this fix | `if "ri" in acdc:` did not crash on a v2 ACDC — it **skipped the TEL query silently**, admitting a registry-backed v2 credential without ever fetching its registry | Registry verification on admit, silently, for v2 ACDCs |

## Areas that diverge, in one line each

Not exhaustive at the hunk level — run the `git diff --stat` above for that.
These are the *areas*, so that a merger sees they exist.

| Area | What it is |
|---|---|
| `src/keri/db/dynamodbing.py` (new, ~1.7k lines) | DynamoDB backend implementing the `LMDBer`/`Baser` store surface, for Lambda. Sets `singleWriter=False`, which is what turns on the first-seen gate in `eventing.py` |
| `src/keri/db/sqlitedbing.py` (new, ~1.5k lines) | SQLite backend over the same surface, for local/dev runs without LMDB |
| `src/keri/db/secretkeeper.py` (new) | Keystore backed by AWS Secrets Manager instead of on-disk files |
| `src/keri/app/lambding.py` (new, ~1k lines) | Lambda entry points and the serverless request/response plumbing |
| `src/keri/core/eventing.py` (~370 lines beyond `processPro`/`processBar`) | `Kever._claimFirstSeen`/`_supersedeFirstSeen` and the `logEvent(supersede=)` gate — KERI first-seen enforced in code for backends that do **not** serialize writers (no-op on LMDB); `Kevery.authenticateMsg` and `anchoringPre` extracted for the `pro`/`bar` path; `LikelyDuplicitousError` → `escrowLDEvent` on both the inceptive and non-inceptive races; `ldes` escrow writes moved to `OnIoDupSuber.add`; message builders (`state`, `query`, `reply`, `prod`, `bare`, `exchept`, `exchange`) default `kind=Kinds.json` because native CESR cannot represent route-like field labels |
| `src/keri/core/parsing.py` | `Ilks.pro`/`Ilks.bar` dispatch (table row above) plus `_attachmentGroupVersion` for genus/version skew detection |
| `src/keri/db/basing.py` | `fetchLastSealingEventBySeal` seal conversion (table row above) **and** the KRAM trans-last-sig store moved from subkey `tsgs.` to `ktsg.` — the fork's KRAM repurposing of `tsgs.` collided with upstream's own `self.tsgs` and broke `rpy`/OOBI routing |
| `src/keri/db/dbing.py` | `LMDBer.MaxNamedDBs` 100 → 200; the fork opens more named sub-DBs than stock and hit `MDB_DBS_FULL` |
| `src/keri/kering.py` | `Schemes` gains `wss` (the serverless mailbox is a WebSocket) |
| `src/keri/vdr/credentialing.py` | `Regery.loadRegistries` repopulates `vcp`/`regd` from the stored TEL inception, so a cold-started process that *loads* a registry can still issue; `Credentialer.create(version=)` passthrough for v1 ACDCs during the v2 transition |
| `src/keri/app/querying.py` | `AnchorQuerier` completion via the general seal finder (table row above) |
| top-level `keri_serviceaid/`, `keri_cdk/`, `ecosystems/`, `examples/` (new packages) | The micro-app runtime, the CDK constructs, the EGF fixtures and the worked examples. Not keripy library code, but they live in this repo and `setup.py` packages them |

## Protocol-version traps this work paid for (keep these; they cost real time)

None of these is a code change — they are facts about this library that are invisible until they
bite, and each one fails **silently**.

- **A version-pinned `Parser` never creates a cue for an off-version message.** It is dropped before
  `Kevery.processPro` runs. A v2 `pro` into a v1-pinned parser yields zero replies and no exception.
- **`Habery` emits a v2 KEL by default.** Replaying it through a `Parser(version=Vrsn_1_0)` logs a
  genus-skew warning, leaves the `Kevery` empty, and raises nothing. KEL replay must use the default
  parser; only the `pro`/`bar` exchange is pinned to `Vrsn_1_0`.
- **`Kevery` accepts a non-local AID's messages under `lax=True, local=False`** — which are its
  *defaults* in this checkout (`eventing.Kevery.__init__`). The tests pass them explicitly as
  documentation and as a guard against a future default flip, not because they are required today.
- **`ProdResponder.service()` returns a `bytearray`,** not a generator. `list()`-ing it iterates
  individual bytes.
- **A v1 `pro` has no `i` field; a v2 one does.** Assertions on `ked["i"]` silently encode a version
  assumption.
- **Three different computations produce a "SAID", and they disagree.** `Diger(ser=...)` digests
  finished bytes. `Saider.saidify` dummies the `d` field first, so it never matches a plain digest.
  And `SerderACDC._compute` — the one that produces a *real* ACDC's SAID — derives it over the most
  compact variant (nested `s`/`a`/`e`/`r` replaced by their own SAIDs, `v` resized), so it does not
  match `Saider.saidify` either. Measured: a v2 ACDC from keripy's own `proving.credential`
  re-derives to a different value under `saidify`. Flat SADs hide this, because for them the two
  agree.
- **`Saider._derive` overwrites `sad["d"]` with a dummy before digesting.** The `d` you passed in is
  never checked. A SAD with `d=None`, `d=12345` or an attacker's SAID verifies just fine unless the
  caller compares `d` itself.
- **`Baser.getEvtPreIter` replays superseded duplicates** (its own docstring says so). Use
  `getEvtLastPreIter` for the accepted KEL. Reading the wrong one means reporting anchors a
  controller has already repudiated by recovery rotation.

## Upstream defects found but not fixed here

- **`ta` vs `td` on `upd`.** The ACDC spec names `ta` once, in the state-registry
  field table, and `td` 51 times; no `upd` example carries `ta`. The requirement
  (a state update names the ACDC it targets) holds under either reading.
- **`pro`/`bar` field order: the KERI spec contradicts itself.** The prose omits
  `i`; the examples include it. keripy v1 matches the prose, v2 the examples —
  faithful to both, so nothing to fix in the library.
- **`bare()` does not enforce the SAID-keyed `a` structure** its docstring
  describes. Callers must not assume the keying; `ProdClient.harvest` checks.
- **A v2 ACDC's SAID does not commit to its own section SAIDs, nor to the size
  digits in `v`.** `SerderACDC._compute` recomputes both before digesting, so
  re-derivation is blind to them. Measured on a real credential: flipping
  `a["d"]` or `e["d"]` to an attacker's value, or corrupting the `v` size,
  leaves `verifySealedBody` at `True`; flipping any *content* correctly gives
  `False`. This is the C3 hole one level down, and it is upstream derivation
  semantics rather than a fork defect, so it is recorded rather than patched —
  a consumer that follows a section SAID out of a verified body should
  recompute it. Nothing in this repo does that today.
