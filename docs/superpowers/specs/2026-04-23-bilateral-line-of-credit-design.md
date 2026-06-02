# Bilateral Line of Credit on KERI — Design Notes

**Status:** Exploratory design memo. Not a roadmap commitment for keripy.
**Scope:** Conceptual design only — no implementation plan attached.
**Audience:** Future authors investigating KERI-native payment primitives.

## Context

Question that prompted this memo: *how would you model a bilateral line of credit (LoC) between two AIDs using only KERI/ACDC primitives — no MyCHIPs spec, no lifts, no new protocols?*

The exploration converged on a design that is small, KERI-idiomatic, and composes with any other ACDC-issuing application that needs payment semantics. This document captures the model so the design space we explored isn't lost.

## Target shape

A bilateral line of credit between Alice and Bob:

- Each extends the other a credit limit (e.g. $500 per side, total exposure $1000).
- Either party can unilaterally draw or deposit within the agreed terms.
- Net balance oscillates around zero; settled balance = zero.
- Either party can initiate close at any time.
- Limit increases require both parties; everything else is unilateral.

This is the same shape as a MyCHIPs tally, minus the lift settlement layer.

## The design

### Tally as a multisig group AID

The line-of-credit relationship is itself an AID: a two-member multisig group whose members are Alice and Bob, with asymmetric current/next thresholds.

- `icount = 2`, `ncount = 2` — both parties contribute keys
- `kt = 1` (current signing threshold) — **either party can sign interaction events alone**
- `nt = 2` (next rotation threshold) — **both parties required to rotate or wind down**

This is the central design move. The asymmetric thresholds give us:

| Operation | Threshold | Result |
|---|---|---|
| Sign `ixn` event (anchors a chit) | `kt = 1` | Unilateral chits |
| Rotate keys / change tally control | `nt = 2` | Bilateral termination |

The tally has its own KEL and its own TEL. Single canonical log. No mirrored TELs to aggregate, no DAG to merge.

### Chit lifecycle: TEL events anchored unilaterally

Each balance-changing transaction ("chit") is an ACDC issued in the tally's TEL. The TEL event is anchored via a seal in an `ixn` event in the tally's KEL, signed by whichever party initiated the action at `kt = 1`.

```
Tally-AID (multisig: Alice, Bob; kt=1, nt=2)
  └── KEL
      ├── icp  (both signed — establishes tally with nt=2)
      ├── ixn  (Alice signed alone — anchors chit #1)
      ├── ixn  (Bob signed alone — anchors chit #2)
      └── ixn  (Alice signed alone — anchors chit #3)
  └── Registry TEL
      ├── vcp  (registry inception)
      ├── iss  (Tally ACDC: terms, limits, Ricardian rules)
      ├── upd  (chit #1 — Alice draws, balance: 0 → +100)
      ├── upd  (chit #2 — Bob deposits, balance: +100 → +50)
      └── upd  (chit #3 — Alice draws, balance: +50 → +150)
```

### Chit schema (ACDC v1 form)

```json
{
  "v": "ACDC10JSON000...",
  "d": "EChit42...",
  "i": "ETallyAID...",
  "ri": "ETallyRegistry...",
  "s": "EChitSchemaSAID...",
  "a": {
    "d": "EAttr...",
    "actor": "EAliceAID...",
    "kind": "draw",
    "amount": 10000,
    "delta": 10000,
    "prev_balance": 5000,
    "new_balance": 15000,
    "settlementRef": null,
    "dt": "2026-04-23T14:30:00Z"
  },
  "e": {
    "d": "EEdge...",
    "tally": { "n": "ETallyACDC...",  "s": "ETallySchema..." },
    "prev":  { "n": "EPrevChit...",   "s": "EChitSchema..."  }
  }
}
```

Notes:

- `actor` names the multisig member who signed; the cryptographic proof is the signature on the anchoring `ixn` event.
- Invariant: `prev_balance + delta == new_balance`. Schema-enforceable.
- `settlementRef` is populated only on deposit chits that settle externally (e.g., `"btc:txid:abc:vout=0"`, `"ach:txref:xyz"`).
- Chits inherit Ricardian terms from the tally via `e.tally`; chits do not carry their own `r`.

### Tally ACDC `a` section (sketch)

The tally ACDC is the genesis credential issued in the tally's TEL. Its `a` section carries the contract terms; chits reference it via `e.tally`.

```json
"a": {
  "d": "ETallyAttr...",
  "partyA": "EAliceAID...",
  "partyB": "EBobAID...",
  "baseUnit": "USD",
  "scale": 2,
  "limit_a_to_b": 50000,
  "limit_b_to_a": 50000,
  "settlementRails": ["ach", "wire", "btc-mainnet"],
  "openedAt": "2026-04-23T14:00:00Z"
}
```

Sign convention: `balance > 0` means partyB owes partyA (drew on partyA's extending credit); `balance < 0` is the reverse. Limits enforce `-limit_a_to_b ≤ balance ≤ +limit_b_to_a`.

### Tally Ricardian section (sketch)

Lives on the tally ACDC's `r` section. Skeleton:

```json
"r": {
  "preamble":        { "l": "Mutual bilateral line of credit between partyA and partyB ..." },
  "limitDiscipline": { "l": "Balance MUST remain in [-limit_a_to_b, +limit_b_to_a] ..." },
  "unilateralActions": {
    "l": "Either party MAY unilaterally: (a) draw within their borrowing limit; (b) deposit any amount; (c) reduce their own extending credit limit."
  },
  "bilateralActions": {
    "l": "REQUIRES nt=2: limit increases, base unit changes, termination, key rotation."
  },
  "settlement": {
    "l": "Outstanding balance settleable via any mutually acceptable means; deposit chit MUST populate settlementRef with evidence."
  },
  "closing": {
    "l": "Either party MAY commence closing by zeroing their extending limit; tally remains in wind-down until balance reaches zero, then bilateral termination via joint rotation."
  },
  "governingLaw": { "l": "..." }
}
```

### Closing

Two-phase, asymmetric:

1. **Wind-down** (unilateral): one or both parties reduce their extending limit to zero. No new draws permitted in that direction; deposit chits continue to flow until balance is zero. This is unilaterally safe — reducing your own credit extension only reduces the other's borrowing capacity.
2. **Termination** (bilateral): once balance is zero, both parties co-sign a rotation event (or `rev` on the tally ACDC) at `nt = 2`. Tally is closed.

Limit *increases* are not unilateral despite `kt = 1` — they are forbidden by the Ricardian `bilateralActions` clause. A `kt = 1` member could technically issue an `ixn` raising the limit, but the counterparty would treat it as material breach and the event would stand as evidence of the violation.

## Key decisions (and why)

| Decision | Choice | Rationale |
|---|---|---|
| Authority model | Multisig AID with `kt=1`/`nt=2` | Unilateral action within bilateral contract — matches real LoC semantics |
| State shape | Single TEL with running balance in each chit | Constant-time balance read from latest event; tamper-evident chain |
| Anchoring | Tally's own KEL | Single canonical log; chits cannot conflict with mirrored siblings |
| Privacy | Group AID's witnesses | AID existence reveals only public key material; balance never on the wire |
| Concurrency | Witness-level first-writer-wins | Races are rare, distinguishable from attacks, retry on rejection |

## Composability — the bigger insight

Because the tally is denominated in a base unit (`a.baseUnit`: `"USD"`, `"BTC"`, `"ETH"`, ...), it is not a special-purpose LoC primitive — it is a **general-purpose KERI-native payment channel between any two AIDs**.

Any ACDC-issuing application that needs payment-evidence can accept a chit SAID as proof of payment:

```
Without tally:
  Alice → external rail → Carol → access ACDC
  paymentRef = "ach:txref:..."  or  "btc:txid:..."

With tally:
  Alice → draw chit on her tally with Carol → access ACDC
  paymentRef = "tally:ETallyAID:EChitSAID"
```

External settlement happens periodically against the tally balance, not per transaction. This is the same two-tier architecture as Lightning Network (channelized credit + bulk settlement) generalized to any base unit and any KERI application.

Use cases this primitive composes with:

- Service-access credentials paid via chits
- Invoice ACDCs settled via chits
- Subscription ACDCs with recurring draw chits
- Usage-metered API access with high-frequency draw chits, settled weekly
- Escrow patterns conditional on chit state

## Out of scope (deferred)

- **Multi-hop / lift settlement** — primitives compose naturally toward this, but designing it requires a separate brainstorm.
- **Wallet, watcher, settlement-adapter implementations** — infrastructure layer not addressed here.
- **Dispute resolution mechanics** — `r.dispute` clause is a placeholder; real legal protocol is jurisdiction-specific.
- **Witness selection strategy** — privacy properties depend on whether parties use shared/generic vs. dedicated witnesses; design choice not addressed.
- **Cryptographic enforcement of limits** — currently policy-enforced via Ricardian contract + counterparty refusal, not protocol-enforced. Could be tightened with custom validators.
- **Concrete keripy API surface** — no `Tallyer`/`Chitter` class design, no module placement.

## Open questions

1. **Limit decrease vs. exposure** — can a party reduce their extending limit *below* current outstanding balance? Probably not (it would invalidate already-recorded chits). Worth specifying.
2. **Chit-reference resolution** — what does `paymentRef = "tally:..."` actually mean operationally? A verifier needs a procedure to resolve a chit SAID, validate the tally, confirm the chit's `delta` matches the expected payment amount, and confirm the chit is "still alive" (tally not revoked).
3. **Idempotency** — two parallel `ixn` events at sn=N are resolved by witnesses (first-writer-wins). What's the user-facing retry contract? Application waits for witness receipts before considering chit final.
4. **Multi-currency tallies** — would a tally with `baseUnit = "USD"` and chits that reference foreign-currency settlements (e.g., Alice deposits BTC worth $100) need an oracle? Probably better to keep tallies single-currency and have separate tallies per currency pair.

## Status

Design captured for future reference. No implementation plan, no implementation. Suitable as input to a future writing-plans cycle if implementation becomes a goal.

## References

- KERI threshold semantics: `kering.py` Tholder; `app/habbing.py:Habery.makeHab` (`isith`, `nsith`, `icount`, `ncount`)
- TEL event types: `vdr/eventing.py` (Ilks.vcp, vrt, iss, rev, bis, brv); ACDC v2 update events `acdc/messaging.py` (Ilks.rip, bup, upd)
- IPEX state machine: `vc/protocoling.py:PreviousRoutes`
- Multisig coordination: `app/grouping.py:Counselor`
- Conversation that produced this memo: 2026-04-23 brainstorming session on bilateral LoC primitives.
