# First-seen claim vs. append — two opposite "concurrent write" mechanisms

This note exists because the witness first-seen gate (`Kever._claimFirstSeen` /
`_supersedeFirstSeen` + the `logEvent` gate in `src/keri/core/eventing.py`) *looks*
like the concurrent-append fix in the `DynamoDBer` (`appendOnVal` /
`_append_at_free_ion` in `src/keri/db/dynamodbing.py`) — both are conditional
writes under concurrency — but they are **semantic opposites**. Conflating them
forks the KEL. Verified against the KERI spec via the keri.host `/chat` RAG
(`keri-specification.html`, `KERI_WP_2.x.web.pdf`).

## The one-line distinction

| | **Witness-receipt accumulation** (IoSet append) | **First-seen claim** (the gate) |
|---|---|---|
| What is racing | N **distinct, all-valid** items (witness A's receipt, B's receipt, …) | competing events for **one** `(pre, sn)` slot |
| Correct outcome | **all get in** — each advances to the next free ordinal and retries until stored | **exactly one** gets in |
| "retry until all in" | ✅ correct — this *is* `_append_at_free_ion` | ❌ a duplicity violation |
| Code | `db.fels.append`, `db.wigs`, `db.rcts`, the escrow IoSets | `_claimFirstSeen` over generic `putVal`/`getVal` |

Spec, on the asymmetry:
> "Each witness also adds to its log any verified signatures from consistent
> receipts it receives from other witnesses." — receipts **accumulate** toward `toad`.
>
> "Excepting superseding recovery, inconsistent receipts, i.e., for different
> event versions at the same location, are discarded (not kept in the log)." —
> a slot admits **one** version.

## What is "aware" of the slot — and is it about content?

The slot `(pre, sn)` is the **controller's**, not the database's:

- The sequence number `sn` is **chosen by the controller**, placed in the event's
  `s` field **before signing**, and the event's SAID (`d`) is a digest over that
  signed content (including `sn` and the prior-event digest `p`).
- Witnesses/validators **cannot change `sn`** — they only verify signatures and
  decide first-seen acceptance. The storage layer's conditional `putVal` enforces
  only "first writer to claim *this exact* `(pre, sn)` wins"; it never invents,
  assigns, or reorders `sn`.

So the slot is **entirely a property of the signed packet**. "Same slot, same
occupant" = byte-identical event (same SAID). "Same slot, *different* occupant" =
two conflicting signed versions of the controller's history at the same point.

## Why you cannot "retry the loser into the next slot"

A losing event `B` was *signed* as `sn=1` with `p` pointing at `sn=0`'s digest.
You cannot relocate it to `sn=2`: its content, SAID, and signature all say `sn=1`.
It is not a free-floating value you can place anywhere — it is a
cryptographically-fixed claim about a *specific* position. Two different events at
one position means the controller (or someone holding its keys) said two
contradictory things. KERI's core guarantee is that this **locks in the first and
turns the second into provable evidence of compromise**, rather than silently
forking history:
> "the first verified version of an event always wins … all other versions are
> discarded" — "first seen, always seen, never unseen."
> "No retry into later sequence numbers — the rejected event cannot be replayed
> later; it's evidence of duplicity."

## The legitimate retries (which the gate allows)

- **Same event re-delivered** (same SAID) → idempotent. It "gets in" only in the
  sense its *receipts accumulate*; no new first-seen, no new `fn`. This retry is
  **controller-side** (the `Receiptor` / `--receipt-endpoint` path re-sends the
  *same* event round-robin until it collects `toad` receipts — KAWA). The witness
  invents nothing. In the gate this is the `existing == serder.saidb → first = False`
  branch.
- **Different event, same slot** (different SAID) → **duplicity, permanently
  rejected.** Escrowed to `ldes` as *evidence*, never receipted. In the gate this
  is the `raise LikelyDuplicitousError` branch → `Kevery.escrowLDEvent`.

## The only legitimate replacement: superseding recovery

A later event may replace a first-seen one **only** via superseding recovery,
under the spec's deterministic rules (universally reconciled by all validators):
a **rotation** may supersede an **interaction** at the same `sn` (it may *not*
supersede another rotation; events already accountable at `toad` cannot be
repudiated). This is recovery from key compromise using unexposed pre-rotated
keys — an explicit trunk/disputed-branch fork, not "retry until in." It is the
*only* path that reaches `_supersedeFirstSeen` (the `setVal` overwrite), gated
behind Kevery's existing recovery decision (`is_supersede = sner.num <= self.sner.num`
in `Kever.update`), never the duplicity path.

## Where this lives in the code

- Gate + routing: `Kever.logEvent` (`if first and not getattr(self.db, "singleWriter", True)`),
  `Kever._claimFirstSeen` / `_supersedeFirstSeen` — `src/keri/core/eventing.py`.
- Duplicity escrow: `Kevery.processEvent` wraps the accept path → `escrowLDEvent`
  (`ldes`) — `src/keri/core/eventing.py`.
- Generic storage verbs (no first-seen concept leaks here): `putVal` (conditional
  insert), `getVal` (strong read), `setVal` (overwrite), and the *separate*
  append family `appendOnVal` / `_append_at_free_ion` — `src/keri/db/dynamodbing.py`.
  The backend advertises only a generic `singleWriter` flag (DynamoDBer = False,
  default True); the KERI layer decides when to enforce first-seen.
- Live proof of the one-winner property under real concurrency:
  `keri_cdk/probes/first-seen/probe.py` (N writers race one slot → exactly one
  winner; every loser observes the winner's said).
- Design + grounding: `docs/superpowers/specs/2026-06-19-witness-ddb-first-seen-concurrency-design.md`.
