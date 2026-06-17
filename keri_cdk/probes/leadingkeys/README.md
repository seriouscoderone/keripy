# LeadingKeys GSI isolation probe

Empirically answers the one UNVERIFIED, SECURITY-CRITICAL question behind the
Service AID pooled-core-table design: **does `dynamodb:LeadingKeys` actually
scope queries on a GSI (`subdb-index`), or only on the base table?**

moto / DynamoDB-Local do not enforce IAM conditions, so this can only be
verified against real AWS. This probe creates throwaway resources, runs the
assertions, and tears them down. It touches none of your existing KERI stacks.

## What it does

1. Creates `lk-probe-<suffix>` DynamoDB table — same schema as `KeriCoreStack.CoreTable`
   (PK/SK + `subdb-index` GSI on `gsi_pk`/`gsi_sk`).
2. Creates two IAM roles (`...-tenanta`, `...-tenantb`), each with the **exact
   production policy statement** from `keri_cdk/service_aid.py`:
   `Query/GetItem/PutItem/DeleteItem/BatchWriteItem/DescribeTable` on the table +
   `index/*`, gated by `ForAllValues:StringLike` on `dynamodb:LeadingKeys`
   = `["{alias}:*#*", "__meta__#{alias}:*"]`. (No `Scan`.)
3. Seeds both tenants with a normal item and a `__meta__` item, reproducing
   `DynamoDBer`'s real key shapes (see `probe.py` header).  The probe also
   seeds the **reachability stores** (`ends.`, `locs.`, `eans.`) into the
   `shared#` namespace (not any tenant's private namespace), reproducing the
   Task 7 oracle change (Task 7 adds these three stores to `SHARED_KEL_STORES`
   in `src/keri/app/lambding.py` so the oracle is reachability-complete: a
   Service-AID can resolve an in-domain peer's mailbox/controller endpoint from
   one local `endsFor` read).
4. Assumes tenant A's role and asserts (the decisive one in **bold**):
   - base table, own PK → ALLOW
   - base table, tenant B's PK → DENY
   - GSI, own `gsi_pk` → ALLOW
   - **GSI, tenant B's `gsi_pk` → must DENY  ← the crux**
   - GSI, the shared `__meta__` `gsi_pk` → must DENY
   - `Scan` (table and index) → DENY
   - GetItem on tenant B → DENY
   - shared `ends.` base read (reachability) → ALLOW (intentionally pooled)
   - shared `locs.` base read (reachability) → ALLOW (intentionally pooled)
   - shared `eans.` base read (reachability) → ALLOW (intentionally pooled)

A tenant whose policy grants `shared#*` CAN read `shared#ends.` /
`shared#locs.` / `shared#eans.` — reachability is intentionally pooled across
all in-domain services. The cross-tenant DENY assertions are unchanged: a
tenant CANNOT read another tenant's PRIVATE namespace.

If the crux is DENIED, the pooled design is sound. If ALLOWED, the index
boundary is vacuous (cross-tenant read) and the design needs rework
(per-tenant tables or per-namespace payload encryption) before pooling.

## Run

```bash
.venv/bin/python probe.py --region <region>          # create, assert, teardown
.venv/bin/python probe.py --region <region> --keep   # leave resources to inspect
.venv/bin/python probe.py --region <region> --teardown-only --suffix <suffix>
```

Requires credentials for the target account (e.g. `AWS_PROFILE=personal`) with
permission to create a DynamoDB table + two IAM roles and to `sts:AssumeRole`
them. Exit code 0 = all assertions passed; 2 = a failure (boundary leak or
misconfig).
```
AWS_PROFILE=personal .venv/bin/python probe.py --region us-east-1
```
