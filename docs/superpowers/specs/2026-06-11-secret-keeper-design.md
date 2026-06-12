# Secret-Backed Keeper — Design Spec

| | |
|---|---|
| Status | Approved for planning (2026-06-11) |
| Repo | keripy fork (`development`), new backend beside `keri/db/dynamodbing.py` |
| Supersedes | The aeid+bran+CMK-on-DynamoDB keeper-hardening path; the `WitnessSaltSecret`/`MailboxSaltSecret` CFN-param salt loading shipped in `0b648b67` |
| Related | `2026-06-10-keeper-custody-aws-findings.md` (§ keeper inventory, "keeper is a cache"), `2026-06-10-keri-service-aid-framework-design.md` §7 (keeper custody) |

## 1. Thesis & goal

The keripy keeper — the AID's **private** key material (root salt, private seeds, pre-rotation seeds, key-state bookkeeping) — is tiny (<2 KB) and, under the `salty` algorithm, effectively a *derived cache*. On the serverless AWS stacks (witness, mailbox, Service AID) it currently lives **in plaintext** in a dedicated DynamoDB `-ks` table. That is the last production gap: private keys readable by anyone with table access.

**Goal:** a new keripy storage backend — a **secret-backed in-memory keeper** — that holds the entire keeper in a single KMS-encrypted AWS secret, loaded into memory at cold start and flushed back only on rare establishment events. This eliminates the plaintext `-ks` table and makes the keeper natively AWS-encrypted, while leaving keripy's `Keeper`/`Manager`/aeid surface **byte-for-byte unchanged** — it is a pure storage substitution, exactly as the fork's `DynamoDBer` already is for the Baser.

This is a keeper-only change. The Baser (large, append-heavy public KEL/TEL state) stays on DynamoDB; the keeper (small, secret, per-AID) moves to the secret store. Right tool per store.

## 2. Verified facts (grounding the design)

- **`Manager.sign` only reads the keeper** (`keeping.py` — `self.ks.pris.get(...)`); it never writes. So the per-request signing path is read-only against the keeper.
- **All keeper writes are establishment-only** — inception, rotation, ingest, and `updateAeid` (`keeping.py` ~1053–1726). Never per-request. So a whole-blob write-back model is viable (writes are rare).
- **`Keeper` subclasses `LMDBer`** — there is no abstract "Keeper interface"; the de-facto interface is the LMDBer/DBer method surface + the Suber wrappers. **The fork already proved a non-LMDB backend works** (`DynamoDBer` + `lambding.setup_keeper` substitutes a DynamoDB store; the `Manager` is none the wiser).
- **The keeper's Subers need only a small KV subset.** For single-sig keepers the stores are `Suber` (gbls, pres, cons-style), `CesrSuber` (prxs, nxts, pres), `CryptSignerSuber` (pris), and `Komer` (prms, sits, pubs). These call only `getVal/setVal/putVal/delVal/getTopItemIter/getValLast/cntVals` — **not** the IoSet/IoDup/ordinal machinery (the `CatCesrIoSetSuber` stores `smids`/`rmids` are group-multisig, empty in v1). Trivial over an in-memory dict.
- **Size growth is bounded and tiny.** Inception keeper ≈ 1–2 KB. Each rotation adds ≈ 100–150 B (one public-key `PubSet` in `pubs`); private seeds do not accumulate (old seed erased on rotate). **Non-transferable AIDs (witness, mailbox) cannot rotate → keeper is fixed at <1 KB forever.** Only transferable Service AIDs grow, ~100–150 B/rotation.

## 3. Settled decisions (from brainstorming, 2026-06-11)

1. **Storage swap, KERI surface unchanged.** New `SecretKeeper` DBer backend; keripy `Manager`/aeid semantics untouched. Mirrors `DynamoDBer`.
2. **Keep bran/aeid.** The keeper's secrets remain keripy-sealed under the aeid (KERI-faithful), *and* the secret store KMS-encrypts the blob on top. The bran is retained for surface consistency, not as an independent trust-domain factor (see §8).
3. **One secret per stack, holding everything.** `keri/<stack-name>/keeper` = a JSON document `{salt, bran, keeper:<zlib-compressed keeper blob>}`, KMS-encrypted by the store.
4. **Compress then store.** Serialize the keeper → `zlib`-compress → store as the secret value (the store KMS-encrypts). Compression targets the growing plaintext public-key history (~3–4×); optional headroom on top of the 64 KB ceiling.
5. **Secrets Manager** is the default store (64 KB headroom for the rotating Service-AID case), behind a thin interface so a given service could be pointed at SSM Parameter Store SecureString later (cost at thousands-of-services scale).
6. **Convention-based, get-or-create at deploy.** No CFN parameter for the secret. The secret is found by the name convention `keri/<stack-name>/keeper`; provisioned get-or-create at deploy via a Custom Resource (operator may pre-create it with their own values; otherwise the stack mints it). The secret lives **outside** the stack's CloudFormation lifecycle → survives `cdk/sam delete` → redeploy reconnects by name → **same AID**.
7. **Per-stack, isolated.** Each keeper secret holds exactly one AID's material; never shared. (Contrast: Baser is pooled for Service AIDs, per-stack for witness/mailbox.)
8. **Offline escrow optional.** Not shown at deploy; the operator can `get-secret-value` anytime for an offline copy. The Secrets Manager copy must persist (the bran is read every cold start; the salt is the cold-recovery anchor).

## 4. Architecture

One new unit plus thin wiring.

**`SecretKeeper` (new, `keri/db/secretkeeper.py` beside `dynamodbing.py`):** an in-memory-dict DBer that implements the small KV method surface the keeper's Subers require (§2). Lifecycle:
- **open / cold start:** `GetSecretValue("keri/<stack>/keeper")` → parse JSON → base64-decode + `zlib`-decompress the `keeper` field → deserialize into the in-memory sub-database dict. (`salt`/`bran` are handed to the runtime, not stored in the keeper dict — see §6.)
- **reads (signing):** served entirely from the in-memory dict — zero AWS calls on the hot path.
- **writes (establishment events only):** mutate the in-memory dict, then re-serialize → compress → **read-modify-write** the secret (`PutSecretValue`), preserving the sibling `salt`/`bran` fields. Rotations are rare and single-flighted.

`lambding.setup_keeper(secret_keeper)` attaches the same Subers — the `Manager` is unchanged.

**Inception & first write happen at deploy, in a Custom Resource**, so the request-path Lambda is **read-only** against the secret:
- Service AID: fold into the **existing inception Custom Resource** (`serviceaid/cdk/inception.py` → `runtime.init`).
- Witness / mailbox (SAM): add a small Custom Resource that runs the handler's `init()` once at deploy.

The request Lambda's IAM is then just `secretsmanager:GetSecretValue` on `arn:…:secret:keri/<stack>/*` + `kms:Decrypt`. The CR's role holds the create/put rights.

## 5. Secret layout & serialization

A single secret per stack at `keri/<stack-name>/keeper`:

```json
{
  "salt": "<qb64 salt>",
  "bran": "<>=21-char passcode>",
  "keeper": "<base64(zlib(json(keeper sub-db dict)))>"
}
```

- `salt` — seeds key derivation at first inception; the recovery/portability anchor (same salt → same AID). Read at inception; thereafter the salt also lives (aeid-encrypted) inside the keeper blob.
- `bran` — the aeid passcode; read at **every** cold start to decrypt the keeper's sealed values.
- `keeper` — the serialized keeper sub-databases (`gbls, pris, prxs, nxts, pres, prms, sits, pubs`, and the unused group stores), with the secret values already aeid-ciphertext (keripy), then zlib-compressed, then base64 for JSON transport. The store KMS-encrypts the whole document at rest.

Serialization format: a dict of `{subdb_name: {hex_key: base64_value}}` (keeper values are bytes/CESR), JSON-encoded, then zlib-compressed, then base64. Versioned by a leading `"v": 1` field for forward compatibility.

## 6. Cold-start & rotation data flow

**Deploy (Custom Resource), once:**
1. get-or-create `keri/<stack>/keeper`: if absent, generate `salt` (qb64) + `bran` (≥21-char random), write `{salt, bran, keeper: <empty>}`.
2. Run inception (`runtime.init` / handler `init`): build `Habery(salt=salt, bran=bran, ks=SecretKeeper(...))` → incept the AID → keripy populates the keeper → `SecretKeeper` flushes `keeper` back into the secret.

**Request cold start (warm Lambda init):**
1. one `GetSecretValue` → `{salt, bran, keeper}`.
2. `bran` → `Habery` (engages aeid); `salt` → `Habery` (ignored if the keeper already carries one — it does post-inception); `keeper` blob → `SecretKeeper` in-memory load.
3. Load Hab from Baser (public state) + keeper (private) → ready. **Signing is pure read** — no further AWS calls.

**Writes — who and when (this preserves §4's read-only request path):**
- **v1's only writer is the deploy-time Custom Resource**, which runs inception once (the single keeper write) with create/put IAM. After that, witnesses and the mailbox are **non-transferable and never rotate**, so their keeper is write-once — the request Lambda is read-only for the life of the stack.
- **Rotation (transferable Service AIDs)** mutates the in-memory keeper (`pris`/`sits`/`pubs`); `SecretKeeper` then re-serializes + compresses + `PutSecretValue` (read-modify-write, preserving `salt`/`bran`). This is an **administrative operation run with write rights (an admin invocation / a rotation CR), not the read-only signing path** — so normal request traffic never needs `PutSecretValue`. Ongoing rotation as a managed flow is **v1.1** (consistent with the Service AID framework spec deferring key-rotation ops); v1 ships the write path but exercises it only at inception.

## 7. DBer method surface implemented by `SecretKeeper`

Only the non-dup, non-ordinal KV subset the keeper Subers call (verified against `subing.py`/`koming.py`):
`getVal, setVal, putVal, delVal, getTopItemIter, getValLast, getValsIter, cntVals, cntAll`, plus `env.open_db` and the `DynamoSubDb`-equivalent sub-database handle, and `close`. The IoSet/IoDup/On* methods are **not** implemented in v1 (the group-multisig keeper stores are empty for single-sig witness/mailbox/Service-AID). A clear `NotImplementedError` guards them so a future group-multisig need fails loudly rather than silently.

## 8. Encryption model & threat posture

- **At rest:** the whole secret document is KMS-encrypted by the store (Secrets Manager, AWS-managed or CMK). This is the primary, AWS-native protection — and it eliminates the plaintext `-ks` DynamoDB table entirely.
- **Inside the blob:** the keeper's secret values (root salt, private seeds) are additionally aeid-sealed by keripy under the bran (KERI-faithful, surface unchanged).
- **Honest limitation (conscious):** because the bran lives in the *same* secret as the keeper it unlocks, the aeid layer is **not an independent factor** — one `GetSecretValue` yields both ciphertext and key. The real boundary is the secret's KMS encryption + tight IAM (`GetSecretValue` scoped to one secret, read-only on the request path, create/put confined to the deploy CR). This is the deliberate "one KMS-protected gate" posture (enterprise-normal); a true second factor would be offline-next-key or KMS-as-signer, both out of scope here.
- **In memory:** as with any in-process signer, the decrypted keeper is in Lambda memory at runtime — unavoidable without KMS-as-signer.

Threat summary: read access to the secret = the AID's keys (same as aeid+bran where both are in AWS). Mitigations: per-service isolation, read-only request-path IAM, create/put confined to the CR, CloudTrail on `GetSecretValue`. Loss of the secret = dead/unrecoverable AID unless an offline copy was escrowed (operator's optional choice).

## 9. Compatibility, migration, supersession

- **Greenfield, no migration.** Existing plaintext `-ks` DynamoDB keepers are not read by the new backend. The 10 deployed stacks are destroyed and replaced (operator-confirmed); same `salt` on redeploy ⇒ same AID.
- **Supersedes** the aeid+bran+CMK-on-DynamoDB hardening (no longer needed — the secret store provides at-rest encryption and removes the table) and the `WitnessSaltSecret`/`MailboxSaltSecret` CFN-param salt loading from `0b648b67` (replaced by the convention-based one-secret get-or-create). The witness/mailbox handlers' `_load_salt` and the salt-secret params are reworked into the `SecretKeeper` + CR flow.
- **Service AID framework:** `runtime.init` swaps its keeper `DynamoDBer` (`-ks` table) for `SecretKeeper`; the `ServiceAid` construct drops the auto-minted bran `sm.Secret` + the `-ks` keeper table, replaced by the single `keri/<alias>/keeper` secret created get-or-create in the inception CR. Baser/Reger on the pooled core table are unchanged.

## 10. Testing

- **Unit:** `SecretKeeper`'s KV surface over an in-memory dict (round-trip get/set/del/iterate); serialize→compress→base64→deserialize round-trip incl. the `"v":1` version field; `NotImplementedError` on the unimplemented IoSet/IoDup methods.
- **Integration (moto):** `Habery(ks=SecretKeeper(...))` against a moto Secrets Manager secret — incept → assert keeper flushed to the secret (compressed); fresh cold start (new SecretKeeper, same secret) → load → **sign** → verify; rotate (transferable) → assert keeper re-flushed and new keys verify; assert the request-path uses no `PutSecretValue` (read-only).
- **Integration (DynamoDB Local + moto SM):** the witness/Service-AID full pipeline with the keeper on a (moto) secret and the Baser on DynamoDB Local — proving the two-store split works end to end.
- **Witness/mailbox handler tests:** `init` get-or-create path; read-only request path.

## 11. v1 scope

**IN:** `SecretKeeper` backend (single-sig KV surface) + `setup_keeper` wiring; one-secret `keri/<stack>/keeper` layout w/ compression + version field; Secrets Manager store behind a thin interface (SSM pluggable); get-or-create at deploy via Custom Resource (witness/mailbox new CR; Service AID existing inception CR); read-only request-path IAM; swap witness + mailbox + Service-AID keepers to `SecretKeeper`; drop the plaintext `-ks` tables, the auto-minted bran secret, and the salt-secret CFN params.

**OUT (later):** SSM Parameter Store as the live default (interface ready, not wired); group-multisig keeper stores (IoSet methods); offline-next-key / KMS-as-signer (separate, parked); keeper-secret pruning of old `pubs` history; automatic bran rotation.

## 12. Open questions (carry into planning, non-blocking)

- Exact module placement and name (`keri/db/secretkeeper.py` vs `keri/app/`); whether the secret-store client (SM vs SSM) selection is an env var or a constructor arg.
- Whether the witness/mailbox keeper-write on first inception should run in the new CR (read-only Lambda — preferred) or be tolerated as a one-time handler write (simpler, slightly broader IAM). Recommend CR.
- Serialization: plain JSON+base64 vs msgpack (smaller, binary). Default JSON for debuggability; revisit if size matters.
- Single-flight mechanism for concurrent rotations (DynamoDB conditional marker, or rely on rotations being manual/serialized). Inherit the Service AID framework's stance.
