# Keeper Custody on AWS — Security Panel Findings (2026-06-10)

Three parallel investigations (threat model / KMS-as-signer feasibility / AWS custody landscape) commissioned after plan review, before implementation. This addends `2026-06-10-keri-service-aid-framework-design.md` §7 and corrects one claim in it.

## Finding 1 — The spec's "no KMS-as-signer path" claim is outdated (CORRECTION)

Spec §7 states: *"No KMS-as-signer path exists in the fork; keripy signs in-process (libsodium Ed25519), so the seed must reach memory regardless."* Both halves are no longer true:

1. **AWS KMS supports pure Ed25519 signing since Nov 2025** — key spec `ECC_NIST_EDWARDS25519`, signing algorithm `ED25519_SHA_512` with `MessageType=RAW` is RFC 8032 pure Ed25519, bit-for-bit what `pysodium.crypto_sign_verify_detached` verifies. A KMS-resident key keeps the standard KERI `D`/`B` prefix codes and `A`/`0B` signature codes — **zero interop cost**. (Caveat: `MessageType=RAW` caps the message at 4096 bytes; `ED25519_PH_SHA_512` is Ed25519ph and is NOT verifiable by libsodium plain verify. Oversized ACDC bodies need SAID-referenced payloads or a P-256/`DIGEST` fallback.)
   - https://aws.amazon.com/about-aws/whats-new/2025/11/aws-kms-edwards-curve-digital-signature-algorithm/
   - https://docs.aws.amazon.com/kms/latest/APIReference/API_Sign.html
2. **keripy already supports ECDSA P-256 end-to-end** (codes `1AAI`/`1AAJ`/`0I`, indexed `E`/`F`/`2E`/`2F`; sign at `signing.py:220-271` incl. DER→raw conversion; verify at `coring.py:3431-3490`), and `Algos.extern` (`keeping.py:43-44`) is a stubbed-but-unimplemented seam for externally-held keys.
3. **The adapter seam is clean.** `Manager.sign` (`keeping.py:1249-1415`) only requires `.sign(ser, index, only, ondex) -> Siger|Cigar` plus `.verfer`. Pre-rotation digests are computed from the *public* key qb64 only (`keeping.py:1032,1226`), so a pre-created next KMS key satisfies pre-rotation. Estimated adapter: `KMSSigner` + `KmsCreator` + ARN-storing suber + `Algos.extern` wiring ≈ **300–500 LOC including tests**, all additive.

**Cost/limits:** $1/key/mo (×2 per service: current + next), $0.15/10k Sign, ~10–50 ms/sign, shared 1,000 req/s ECC quota per account/region (adjustable). ≈ **$2.40/service/month** at 1k services.

## Finding 2 — The DynamoDB keeper is a cache, not a store of record

With keripy's default `algo='salty'` (`SaltyCreator`, `keeping.py:469-550`), **every private key past/present/future is deterministically re-derivable from the root salt** + indices that are reconstructible from the public KEL. Inventory of the ten keeper stores for a single-AID service: exactly two secret values exist (root salt, stored twice; private seeds, derivable from the salt). Everything else is public bookkeeping; four stores are empty in this deployment. Total keeper size < 2 KB.

Consequences:
- The keeper **could be folded into the existing per-service Secrets Manager secret** (`{bran, salt}` or the serialized keeper) — zero marginal cost, deletes the per-service `-ks` table, the DDB-backup exfil channel, and a resource class that collides with the **2,500-table regional DynamoDB quota** at "thousands of services" scale.
- `Manager.rotate` is **not concurrency-safe** in any backend (unguarded read-modify-write, `keeping.py:1232-1244`). Salty derivation is the safety net: the witnessed KEL is ground truth and keys re-derive from the salt. Rotations must be serialized (single-flight; run through the Custom Resource) regardless of storage choice.
- v1 as planned (DDB keeper) is the lowest-code-risk path since `lambding.setup_keeper` exists; **keeper-in-SM is the principled v1.1 target**.

## Finding 3 — Threat model verdict and the one hardening that matters

v1 (DDB sealed-box ciphertext + SM bran, tight IAM) is **sound for launch** against the realistic accidental-exposure classes: DDB-only read gives ciphertext (useless without the bran-derived X25519 key); SM-only read gives a decryption capability with nothing to decrypt. Launch conditions: machine-generated ≥128-bit brans; serialized rotations; CloudTrail alarms on non-function-role `GetSecretValue`/keeper reads; SCP-deny on `-ks` table exports.

The structural weakness: **current keys and pre-rotation next keys live under the same secret** (same `pris` store, same aeid; under salty they all collapse into one salt). So total cloud compromise (both stores, code-exec in Lambda, account admin, AWS insider) is **unrecoverable** — the attacker holds the pre-committed next keys too, and KERI's superseding-rotation recovery cannot disown them.

**Highest-leverage hardening: move next-key custody out of the AWS blast radius.** Derive next-key digests from a second salt held offline by the operator; supply only public-key digests at incept/rotate. Hooks exist (`Manager.ingest` `keeping.py:1480+`, custodial `ondices` signing `keeping.py:1290-1304`). This converts cloud compromise from "AID permanently hijacked" into "execute a superseding rotation from the offline key and carry on." Note this remains true even with KMS-resident keys — an account-admin attacker can't *exfiltrate* a KMS key but can *use* it once to sign a rotation committing to attacker keys; only an out-of-band next key defeats that.

## Custody ladder (us-east-1 pricing, June 2026, annualized at 1k services)

| Tier | Architecture | Cost @ 1k svcs/yr | Key property |
|---|---|---|---|
| **BASELINE** | SSM standard SecureString per service + 1 shared CMK; bran fetched in-handler via Parameters/Secrets Lambda extension | ~$20–60 | Nearly free; 10k-param + 40 TPS ceilings |
| **HARDENED** (best ratio) | Per-service data-key envelope under 8–16 sharded CMKs with `EncryptionContext={service}` IAM conditions; ciphertext in DDB row | ~$150–400 | Cryptographic per-service blast radius; per-service audit via encryption context in CloudTrail; survives 10k services |
| **v1 as planned** | Secrets Manager bran + DDB keeper (aeid sealed-box) | ~$4,800 (SM dominates) | Built-in rotation/resource policies; simplest given existing code |
| **MAXIMUM (serverless ceiling)** | Per-service **KMS Ed25519 keys**; signing never leaves FIPS 140-3 L3 HSM; keeper holds no secrets at all (ARNs + bookkeeping only); no bran/SM needed | ~$24,600 + $0.15/10k signs | Non-extractable keys; every Sign CloudTrail-audited; ~10–50 ms/sign; 1,000 rps shared ECC quota |
| CloudHSM | HA pair ~$28k/yr flat | — | Does NOT fit: shared trust domain for all services, VPC+daemon hostile to Lambda, KMS custom key stores don't support asymmetric keys anyway; KMS itself is now FIPS 140-3 L3 |
| Nitro Enclaves | EC2-only (never Lambda/Fargate) | — | The pattern if a non-Lambda signing sidecar ever exists: attestation-gated KMS key release (`kms:RecipientAttestation:ImageSha384`) |

**Plainly:** if signing stays in-process on Lambda, "key never extractable" is unachievable — the honest ceiling on Lambda is KMS-side signing.

## Lambda-specific rules (apply to all tiers)

- **SnapStart snapshots memory and disk** and reuses the snapshot across environments. Secrets must be fetched and keys derived in the INVOKE phase, never at module import. (The framework's `runtime.init()` is invoked from the handler, not at import — already compliant; keep it that way.)
- **Never put secrets in env vars** (`lambda:GetFunctionConfiguration` exposure).
- Use the **AWS Parameters and Secrets Lambda Extension** (free layer, localhost cache, 300 s TTL) for bran fetches.

## Operational requirements (any tier)

- **Escrow is mandatory**: a lost bran/salt = permanently dead AID (no KERI recovery without current or next keys). On service creation, encrypt the bran to an offline public key (age/GPG, hardware-token-held), store in object-locked S3 in a second account, test restore quarterly.
- Alarms: `GetSecretValue`/`Decrypt`/`Sign` from unexpected principals; `ScheduleKeyDeletion`/`DisableKey`/`PutKeyPolicy`; volume anomaly detection per key.
- Bran rotation: on suspicion/departure or ~24 months (cheap — `updateAeid` re-encrypts in place, no KEL event); KERI key rotation is governed by the KEL on its own schedule.

## Disposition (proposed)

- **v1 (this plan): unchanged** — SM bran + aeid + DDB keeper, plus the launch conditions above folded into Task 7/12 details where cheap.
- **v1.1: keeper-in-Secrets-Manager** — delete the per-service `-ks` DDB table; keeper rehydrates from `{bran, salt}` + the KEL (salty derivation).
- **v2: KMS-as-signer tier** — `Algos.extern` + `KMSSigner` adapter; the high-assurance option (possibly default for publisher-grade AIDs). Strike the spec §7 claim (done via addendum).
- **v2: offline next-key custody** — the governance/kill-switch story (spec §14) should incorporate this; it is the only measure that makes total-cloud-compromise recoverable.
