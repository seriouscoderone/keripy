# Fork divergence from WebOfTrust/keripy

Upstream does not accept AI-authored contributions, so these changes are
permanent. A future upstream merge MUST preserve every row below; each has
tests that fail without it.

| Change | Commit | Why | What breaks if dropped |
|---|---|---|---|
| `Baser.fetchLastSealingEventBySeal` converts the caller's seal dict before comparing | `d09ca318` | It compared a `dict` to a `namedtuple`, so it returned `None` for **every** seal shape. Its two siblings do convert. | Anchor lookup by digest; `AnchorQuerier` hangs; `AnchorWatcher` and the whole trigger path |
| `AnchorQuerier` completion calls the general seal finder | `d09ca318` | It called the `SealEvent`-only finder, so it never terminated on a `SealDigest` anchor | Waiting for a digest anchor never returns |
| `parsing.py` dispatches `Ilks.pro` / `Ilks.bar` | `51a5deef` | There was no branch at all — a signed `pro` died at `msgProcess`'s fall-through `else` with `Unexpected message ilk`, so the handlers were unreachable from the wire | All prod/bare retrieval |
| `Kevery.processPro` / `processBar` implemented | `51a5deef` | Both were `pass` stubs | All prod/bare retrieval |
| `keri/app/prodding.py` — default-deny disclosure responder | `bb7fda62`, `a742dc96` | Nothing answered a prod. The policy takes the prod itself so it can gate on `q["az"]`; a raising policy fails closed and does not stall the cue drain | Retrieval, and the disclosure-policy safety properties |
| `keri/app/anchoring.py` — `AnchorWatcher` | this plan | The queriers only wait for *known* anchors; nothing reports new ones | Log-triggered micro-apps |
| `keri/core/sealing.py` — `verifySealedBody` | this plan | The trust property for log-triggered retrieval, dual-mode: saidify for SADs, plain digest for opaque blobs | Bodies would be trusted by sender rather than by re-derivation; a saidify-blind verifier rejects **every valid ACDC** |
| `keri/app/prodding.py` — `ProdClient` | this plan | Nothing in `src/` ever *sent* a `pro`. Defaults `pvrsn=Vrsn_1_0` to match `ProdResponder` | Retrieval requests; a version mismatch yields **silence**, not an error |

## Protocol-version traps this work paid for (keep these; they cost real time)

None of these is a code change — they are facts about this library that are invisible until they
bite, and each one fails **silently**.

- **A version-pinned `Parser` never creates a cue for an off-version message.** It is dropped before
  `Kevery.processPro` runs. A v2 `pro` into a v1-pinned parser yields zero replies and no exception.
- **`Habery` emits a v2 KEL by default.** Replaying it through a `Parser(version=Vrsn_1_0)` logs a
  genus-skew warning, leaves the `Kevery` empty, and raises nothing. KEL replay must use the default
  parser; only the `pro`/`bar` exchange is pinned to `Vrsn_1_0`.
- **`Kevery` needs `lax=True, local=False`** to accept a non-local AID's messages.
- **`ProdResponder.service()` returns a `bytearray`,** not a generator. `list()`-ing it iterates
  individual bytes.
- **A v1 `pro` has no `i` field; a v2 one does.** Assertions on `ked["i"]` silently encode a version
  assumption.
- **`Saider.saidify` and `Diger(ser=...)` are different computations.** A SAD's SAID dummies the `d`
  field before digesting. A plain digest over the finished bytes never matches.

## Upstream defects found but not fixed here

- **`ta` vs `td` on `upd`.** The ACDC spec names `ta` once, in the state-registry
  field table, and `td` 51 times; no `upd` example carries `ta`. The requirement
  (a state update names the ACDC it targets) holds under either reading.
- **`pro`/`bar` field order: the KERI spec contradicts itself.** The prose omits
  `i`; the examples include it. keripy v1 matches the prose, v2 the examples —
  faithful to both, so nothing to fix in the library.
- **`bare()` does not enforce the SAID-keyed `a` structure** its docstring
  describes. Callers must not assume the keying; `ProdClient.harvest` checks.
