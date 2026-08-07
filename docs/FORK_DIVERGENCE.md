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
| `keri/app/prodding.py` — `pro`/`bar` honour the RETURN ROUTE `rr` | this fix | The pair ignored `rr` on both sides: `ProdClient.request` had no `replyRoute` parameter and so emitted `rr: ""`, and `ProdResponder.respond` built its `bar` with `route=cue["route"]` — the prod's `r`. But `rr` is the field that "allows a message to indicate how to target the associated response… on asynchronous transports" (keri-specification.md:2709-2713), and the spec's only worked prod/bare example answers `pro.rr` of `/confidential/process` with a `bar` whose `r` is `/confidential/process` (:2841 → :2888). Answering on the prod's own `r` returns the disclosure down the REQUEST channel. `ProdClient` now defaults `rr` to `f"{route}/process"`, mirroring the example's convention; `ProdResponder` routes the `bar` on the prod's `rr`, falling back to `r` when a requester sent none — the spec's own `MAY`/`REQUIRED` contradiction (see below) means a prod without `rr` is not malformed, while `bar.r` IS required (:2864-2865) and must be filled with something | Response correlation on any asynchronous transport: with several prods outstanding a requester cannot tell which disclosure answers which request. Also `bar.r` becomes unconstructible from a `pro` carrying `rr: ""` |
| `keri/vc/proving.py` — `credential()` picks the registry field by version | this fix | It wrote `vc["ri"] = status` unconditionally. ACDC **v2 renamed `ri` → `rd`** and the v2 top-level field domain is `strict=True`, so **every** registry-backed v2 credential raised `SerializeError: Unallowed extra field(s) = ['ri']` — including the default call path, because `credential()`'s own `version` default resolves to v2. The vendored ACDC v1.1 spec uses `rd` throughout and mentions `ri` **zero times**. Verified present on upstream `main` @ `f4b9e3e8` too, so this is an upstream gap, not fork drift | Any v2 registry-backed ACDC. Callers had to know to pass `version=Vrsn_1_0` |
| `keri/cli/commands/ipex/admit.py` — TEL query reads `ri` **or** `rd` | this fix | `if "ri" in acdc:` did not crash on a v2 ACDC — it **skipped the TEL query silently**, admitting a registry-backed v2 credential without ever fetching its registry | Registry verification on admit, silently, for v2 ACDCs |

| `keri/vdr/eventing.py` — `Reger.cloneTvtAt` guards a missing TEL event | this fix | It read `dig = self.tels.get(keys=pre, on=sn)` and passed the result straight to `cloneTvt`. With no event at that `sn`, `dig` is `None` and `dgKey(pre, None)` dies in string formatting: `TypeError: %b requires a bytes-like object … not 'NoneType'`. So a caller asking about a credential whose TEL has not arrived yet — the NORMAL state while a log-triggered watch is catching up, since the seal is precisely what tells you a body exists — got a TypeError out of a DB accessor instead of the module's own `MissingEntryError`, which `cloneTvt` already raises for an event whose raw bytes are absent. Now raises `MissingEntryError`; the dead `snkey = snKey(pre, sn)` line goes with it. Upstream code (`26f754217`, Samuel M Smith), so an upstream gap rather than fork drift | Any caller polling a TEL that may not have landed. Measured in the actuarial HOA: the exception propagated out of the actuary page's scan loop and killed the ENTIRE pass — the mandate had already been retrieved, verified and recorded, and the list still rendered empty forever because a later unrelated seal raised before the repaint |
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
| `src/keri/core/parsing.py` | `Ilks.pro`/`Ilks.bar` dispatch (table row above) plus `_attachmentGroupVersion` for genus/version skew detection, **and** `exts.setdefault("source", None)` in the `Ilks.qry` branch of `msgProcess` — `source` is not a declared `MsgParseDom` field, so `asdict()` never yields it, and the branch set it only under `if exts['lsgs']:` while evaluating `exts['source']` unconditionally. Every query signed with NonTransReceiptCouples (i.e. by any **non-transferable** identifier) therefore died on `KeyError('source')` before `Kevery.processQuery` was reached — measured directly, `cues: []`. The `pro`/`bar` branch this fork added immediately below already setdefaults it, and `eventing.py`'s match-based twin uses `kwa.get('source')`; the `qry` branch was the drifted one. Found on a live witness-less direct-mode deployment: 896 queries sent, 896 message-less "Parser msg extraction error", zero replays. Test: `tests/core/test_parsing.py::test_query_signed_by_non_transferable_reaches_processQuery`. **Not fixed here:** the same branch still lacks the `elif exts['tsgs']` arm its `pro`/`bar` sibling has, so a query endorsed with `last=False` raises `ValidationError` rather than being processed — a loud failure, unlike this silent one |
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
- **Routed-message field order: the KERI spec contradicts itself, on ALL FOUR types.**
  Every field-order sentence omits `i` while every corresponding example includes it —
  `qry` (:2727-2728 vs :2742), `rpy` (:2772-2773 vs :2788), `pro` (:2816-2817 vs :2838),
  `bar` (:2864-2865 vs :2887). Verified by reading all eight sites. keripy v1 matches the
  prose, v2 the examples — faithful to both, so nothing to fix in the library.
  *(This entry previously described the contradiction as a `pro`/`bar` quirk. It is not;
  it is systematic across the routed-message class.)*
- **`rr` (Return Route): the KERI spec contradicts itself about whether it is mandatory.**
  The general rule at :2486-2487 — "The Routed Messages MUST include a route, `r` field,
  and **MAY** include a return route, `rr` field" — against the per-body rules at :2728
  (`qry`) and :2817 (`pro`), both of which list `rr` and say "All are REQUIRED". Two
  MUST-level sentences that disagree. We send a real `rr`, which satisfies either reading.
  See the `prodding.py` row in the table above for the behaviour this fixed.
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

- **`Diger(ser=b"")` raises `EmptyMaterialError` instead of digesting empty
  input.** `Diger.__init__` (`src/keri/core/coring.py:3735-3739`) wraps its
  `Matter` init in `except EmptyMaterialError: if not ser: raise ex`, which
  cannot distinguish "no `ser` was supplied" from "`ser` was supplied and is
  empty". Blake3 of the empty string is perfectly well defined, and a genuinely
  0-byte file has a genuine digest, so refusing it is wrong for any caller
  hashing real files. Hit for real: `ipd-parse` emits an empty
  `parse-report.jsonl`, and digesting a parse directory crashed on it.
  Not patched here, because changing `Matter`'s empty-material convention has
  a blast radius well beyond this need. The workaround, used by
  `ugard/insurance-product/parser/src/ipd/manifest.py`, is to go through the
  classmethod directly — `Diger(raw=Diger._digest(b, code=DigDex.Blake3_256),
  code=DigDex.Blake3_256)` — verified byte-identical to `Diger(ser=b)` for
  non-empty input across ASCII, multi-byte UTF-8 and all 256 byte values, and
  verified to produce exactly `blake3(b"").digest()` for empty input. Any other
  caller that digests file contents will hit this too.
