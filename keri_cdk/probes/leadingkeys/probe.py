#!/usr/bin/env python3
"""Empirical probe: does `dynamodb:LeadingKeys` actually scope GSI (index) queries?

The Service AID pooled-core-table multi-tenant boundary rests on an IAM policy
that grants DynamoDB Query/GetItem/etc. on the table AND its `subdb-index` GSI,
gated by a `dynamodb:LeadingKeys` condition scoped to the tenant's namespace.
moto and DynamoDB-Local do NOT enforce IAM conditions, so the boundary has
NEVER been verified against real AWS. This script does that, end to end:

  1. create a throwaway table (PK/SK + `subdb-index` GSI) — same schema as
     `KeriCoreStack.CoreTable`;
  2. create two IAM roles (tenant A / tenant B) each carrying the EXACT
     production policy statement, scoped to its own namespace via LeadingKeys;
  3. seed items for both tenants — both a normal item and a `__meta__` item,
     reproducing `DynamoDBer`'s real key shapes;
  4. assume each role and run a battery of allow/deny assertions — the decisive
     one being a CROSS-TENANT GSI query (tenant A's role querying tenant B's
     `gsi_pk`). If that is DENIED, the pooled design is sound; if ALLOWED, the
     index boundary is vacuous (cross-tenant read) and the design needs rework.

Everything is created with a unique `lk-probe-<suffix>` prefix and torn down at
the end (unless --keep). It touches NONE of your existing KERI stacks/tables.

Usage:
  python probe.py --region us-east-1            # create, assert, teardown
  python probe.py --region us-east-1 --keep     # leave resources for inspection
  python probe.py --region us-east-1 --teardown-only --suffix <suffix>  # clean leftovers

Real key shapes reproduced (src/keri/db/dynamodbing.py:344-494):
  normal item : PK = "{ns}#{subdb}#{hex}"   gsi_pk = "{ns}#{subdb}"   gsi_sk = "{hex}"
  meta   item : PK = "__meta__#{ns}#{subdb}" gsi_pk = "__meta__"      gsi_sk = "{ns}#{subdb}"
Production LeadingKeys patterns — the shared-KEL oracle four-pattern union
(keri_cdk/service_aid.py + witness_stack.py + mailbox_stack.py):
  ["shared#*", "__meta__#shared#*", "{alias}:*#*", "__meta__#{alias}:*"]
The shared#* grant makes the pooled public KEL readable+writable by every tenant
(the oracle); the {alias}:* grants keep each tenant's PRIVATE namespace isolated.
"""
import argparse
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError

GSI_NAME = "subdb-index"
SUBDB = "evts"                       # a representative store name
# Two tenants, mirroring Service AID aliases. Namespace = "{alias}:kel" (the
# Service AID pools baser as "{alias}:kel"). Aliases chosen so neither is a
# prefix of the other (tenanta / tenantb), so a StringLike "tenanta:*" pattern
# cannot accidentally match tenantb.
TENANTS = {
    "A": {"alias": "tenanta", "ns": "tenanta:kel"},
    "B": {"alias": "tenantb", "ns": "tenantb:kel"},
}


def hexk(s: str) -> str:
    return s.encode().hex()


def normal_item(ns: str, key: str) -> dict:
    return {
        "PK": {"S": f"{ns}#{SUBDB}#{hexk(key)}"},
        "SK": {"S": "."},
        "gsi_pk": {"S": f"{ns}#{SUBDB}"},
        "gsi_sk": {"S": hexk(key)},
        "val": {"S": f"secret-of-{ns}"},
    }


def meta_item(ns: str) -> dict:
    return {
        "PK": {"S": f"__meta__#{ns}#{SUBDB}"},
        "SK": {"S": "__meta__"},
        "gsi_pk": {"S": "__meta__"},          # BARE constant — not namespaced
        "gsi_sk": {"S": f"{ns}#{SUBDB}"},
        "val": {"S": f"meta-of-{ns}"},
    }


# The shared-KEL "oracle" namespace (no tenant alias). Both tenant policies grant
# `shared#*` + `__meta__#shared#*`, so the pooled public-KEL store is readable AND
# writable by every tenant — that is the oracle, and the boundary below proves it
# coexists with strict per-tenant isolation of the PRIVATE namespaces.
SHARED_NS = "shared"


def shared_normal_item(key: str) -> dict:
    return {
        "PK": {"S": f"{SHARED_NS}#{SUBDB}#{hexk(key)}"},
        "SK": {"S": "."},
        "gsi_pk": {"S": f"{SHARED_NS}#{SUBDB}"},
        "gsi_sk": {"S": hexk(key)},
        "val": {"S": "pooled-key-event"},
    }


def shared_meta_item() -> dict:
    return {
        "PK": {"S": f"__meta__#{SHARED_NS}#{SUBDB}"},
        "SK": {"S": "__meta__"},
        "gsi_pk": {"S": "__meta__"},          # BARE constant — not namespaced
        "gsi_sk": {"S": f"{SHARED_NS}#{SUBDB}"},
        "val": {"S": "meta-of-shared"},
    }


def leading_keys_policy(table_arn: str, alias: str) -> dict:
    """The EXACT production statement (keri_cdk/service_aid.py), verbatim shape."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "dynamodb:DescribeTable",
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
                "dynamodb:BatchWriteItem",
            ],
            "Resource": [table_arn, f"{table_arn}/index/*"],
            "Condition": {
                "ForAllValues:StringLike": {
                    # the four-pattern oracle union (keri_cdk/service_aid.py +
                    # witness_stack.py + mailbox_stack.py): shared oracle namespace
                    # PLUS this tenant's own private namespace.
                    "dynamodb:LeadingKeys": [
                        "shared#*", "__meta__#shared#*",
                        f"{alias}:*#*", f"__meta__#{alias}:*",
                    ]
                }
            },
        }],
    }


# ── classification ───────────────────────────────────────────────────────────
ALLOW, DENY, ERROR = "ALLOW", "DENY", "ERROR"


def classify(fn):
    """Run a DynamoDB call; return (verdict, detail)."""
    try:
        resp = fn()
        n = resp.get("Count", resp.get("Items") and len(resp["Items"]) or 0)
        return ALLOW, f"ok (Count={n})"
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("AccessDeniedException",) or "not authorized" in str(e):
            return DENY, code
        return ERROR, code
    except Exception as e:  # noqa: BLE001
        return ERROR, repr(e)


# ── setup ────────────────────────────────────────────────────────────────────
def create_table(ddb, table):
    print(f"  creating table {table} ...")
    ddb.create_table(
        TableName=table,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "gsi_pk", "AttributeType": "S"},
            {"AttributeName": "gsi_sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": GSI_NAME,
            "KeySchema": [
                {"AttributeName": "gsi_pk", "KeyType": "HASH"},
                {"AttributeName": "gsi_sk", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )
    ddb.get_waiter("table_exists").wait(TableName=table)
    print("  table active")


def seed(ddb, table):
    for t in TENANTS.values():
        ddb.put_item(TableName=table, Item=normal_item(t["ns"], "event0"))
        ddb.put_item(TableName=table, Item=meta_item(t["ns"]))
    # the shared oracle namespace: one pooled item + its meta, written ONCE (no
    # tenant owns it). A read of these by tenant A proves cross-writer visibility.
    ddb.put_item(TableName=table, Item=shared_normal_item("event0"))
    ddb.put_item(TableName=table, Item=shared_meta_item())
    print(f"  seeded {len(TENANTS)} tenants (normal + meta each) + the shared oracle namespace")


def create_role(iam, role_name, account, table_arn, alias):
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            # account root → delegates to IAM; the (admin) caller can assume.
            "Principal": {"AWS": f"arn:aws:iam::{account}:root"},
            "Action": "sts:AssumeRole",
        }],
    }
    iam.create_role(RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(trust),
                    Description="LeadingKeys probe - throwaway, safe to delete")
    iam.put_role_policy(RoleName=role_name, PolicyName="leadingkeys",
                        PolicyDocument=json.dumps(leading_keys_policy(table_arn, alias)))
    print(f"  created role {role_name} (alias={alias})")


def assumed_ddb(sts, role_arn, region, retries=20):
    """Assume the role (retrying IAM propagation) → a namespaced DynamoDB client."""
    last = None
    for _ in range(retries):
        try:
            c = sts.assume_role(RoleArn=role_arn, RoleSessionName="lkprobe")["Credentials"]
            return boto3.client(
                "dynamodb", region_name=region,
                aws_access_key_id=c["AccessKeyId"],
                aws_secret_access_key=c["SecretAccessKey"],
                aws_session_token=c["SessionToken"],
            )
        except ClientError as e:
            last = e
            time.sleep(3)
    raise RuntimeError(f"could not assume {role_arn}: {last}")


def wait_policy_effective(client, table, ns):
    """Gate on IAM eventual consistency: retry the SAME-TENANT base query until it
    succeeds, so subsequent DENY verdicts are trustworthy (not propagation lag)."""
    pk = f"{ns}#{SUBDB}#{hexk('event0')}"
    for _ in range(20):
        v, _d = classify(lambda: client.query(
            TableName=table,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": {"S": pk}}))
        if v == ALLOW:
            return True
        time.sleep(3)
    return False


# ── the assertions ─────────────────────────────────────────────────────────
def run_assertions(client_a, table):
    """All run as tenant A's role. (alias=tenanta, ns=tenanta:kel)."""
    a, b = TENANTS["A"]["ns"], TENANTS["B"]["ns"]

    def q_base(pk):
        return client_a.query(TableName=table, KeyConditionExpression="PK = :pk",
                              ExpressionAttributeValues={":pk": {"S": pk}})

    def q_gsi(gpk):
        return client_a.query(TableName=table, IndexName=GSI_NAME,
                              KeyConditionExpression="gsi_pk = :g",
                              ExpressionAttributeValues={":g": {"S": gpk}})

    checks = [
        # name, callable, expected, why-it-matters
        ("base: own PK (tenanta)",
         lambda: q_base(f"{a}#{SUBDB}#{hexk('event0')}"), ALLOW,
         "same-tenant read must work"),
        ("base: cross-tenant PK (tenantb)",
         lambda: q_base(f"{b}#{SUBDB}#{hexk('event0')}"), DENY,
         "LeadingKeys must block the base table"),
        ("GSI: own gsi_pk (tenanta)",
         lambda: q_gsi(f"{a}#{SUBDB}"), ALLOW,
         "same-tenant index read must work"),
        ("GSI: cross-tenant gsi_pk (tenantb)  <<< THE CRUX",
         lambda: q_gsi(f"{b}#{SUBDB}"), DENY,
         "does LeadingKeys enforce on the GSI partition key?"),
        ("GSI: shared __meta__ gsi_pk",
         lambda: q_gsi("__meta__"), DENY,
         "the bare-constant meta index must not enumerate all tenants"),
        ("base: Scan (not granted)",
         lambda: client_a.scan(TableName=table), DENY,
         "no scan escape hatch"),
        ("GSI: Scan (not granted)",
         lambda: client_a.scan(TableName=table, IndexName=GSI_NAME), DENY,
         "no index-scan escape hatch"),
        ("base: GetItem cross-tenant (tenantb)",
         lambda: client_a.get_item(TableName=table, Key={
             "PK": {"S": f"{b}#{SUBDB}#{hexk('event0')}"}, "SK": {"S": "."}}), DENY,
         "point read must be blocked too"),
        # ── write path ────────────────────────────────────────────────────
        ("base: own PutItem (tenanta)",
         lambda: client_a.put_item(TableName=table, Item=normal_item(a, "writetest")), ALLOW,
         "same-tenant write must work (control)"),
        ("base: cross-tenant PutItem (tenantb)  <<< WRITE CRUX",
         lambda: client_a.put_item(TableName=table, Item=normal_item(b, "poison")), DENY,
         "must not write into another tenant's namespace"),
        ("base: cross-tenant DeleteItem (tenantb)",
         lambda: client_a.delete_item(TableName=table, Key={
             "PK": {"S": f"{b}#{SUBDB}#{hexk('event0')}"}, "SK": {"S": "."}}), DENY,
         "must not delete another tenant's records"),
        ("base: cross-tenant BatchWriteItem (tenantb)",
         lambda: client_a.batch_write_item(RequestItems={
             table: [{"PutRequest": {"Item": normal_item(b, "poison2")}}]}), DENY,
         "bulk write path must be scoped too"),
        # ── shared oracle namespace (pooled KEL — readable + writable by ALL) ──
        ("shared: base read of pooled item (written by no tenant)  <<< ORACLE READ",
         lambda: q_base(f"{SHARED_NS}#{SUBDB}#{hexk('event0')}"), ALLOW,
         "any tenant must read the shared key-state oracle (cross-writer visibility)"),
        ("GSI: shared gsi_pk",
         lambda: q_gsi(f"{SHARED_NS}#{SUBDB}"), ALLOW,
         "shared-store index read is shared-by-design"),
        ("shared: own PutItem into the oracle  <<< ORACLE WRITE",
         lambda: client_a.put_item(TableName=table, Item=shared_normal_item("writetest")), ALLOW,
         "any tenant must write its public KEL into the shared pool"),
        ("shared: meta base read (__meta__#shared#...)",
         lambda: client_a.get_item(TableName=table, Key={
             "PK": {"S": f"__meta__#{SHARED_NS}#{SUBDB}"}, "SK": {"S": "__meta__"}}), ALLOW,
         "shared-store meta row is readable under __meta__#shared#*"),
    ]

    rows, ok = [], True
    for name, fn, expected, why in checks:
        verdict, detail = classify(fn)
        passed = verdict == expected
        ok = ok and passed
        rows.append((name, expected, verdict, "PASS" if passed else "**FAIL**", detail, why))
    return rows, ok


def print_report(rows, ok):
    print("\n" + "=" * 100)
    print("LEADINGKEYS GSI ISOLATION PROBE — RESULTS (all calls run as tenant A's role)")
    print("=" * 100)
    for name, expected, verdict, status, detail, why in rows:
        print(f"  [{status:>8}] {name}")
        print(f"             expected={expected:5} got={verdict:5}  ({detail})  — {why}")
    print("-" * 100)
    crux_leaks = [r for r in rows if "CRUX" in r[0] and r[2] == ALLOW]
    if ok:
        print("VERDICT: ✅ LeadingKeys ENFORCES the multi-tenant boundary on the GSI AND the")
        print("         write path, AND the shared-KEL oracle coexists with it: cross-tenant")
        print("         PRIVATE reads/index-reads/writes are all DENIED; same-tenant ops and")
        print("         the SHARED oracle namespace (read+write, cross-writer) all ALLOW.")
    else:
        print("VERDICT: ❌ One or more assertions FAILED. Inspect above.")
        for r in crux_leaks:
            print(f"         CRITICAL: a cross-tenant CRUX op was ALLOWED — {r[0]}")
        if crux_leaks:
            print("         The boundary is VACUOUS for that path. The pooled design leaks across")
            print("         tenants and MUST be reworked (per-tenant tables, or per-namespace")
            print("         payload encryption) before pooling anything multi-tenant.")
    print("=" * 100 + "\n")


# ── teardown ─────────────────────────────────────────────────────────────────
def teardown(ddb, iam, table, role_names):
    for r in role_names:
        try:
            iam.delete_role_policy(RoleName=r, PolicyName="leadingkeys")
        except ClientError:
            pass
        try:
            iam.delete_role(RoleName=r)
            print(f"  deleted role {r}")
        except ClientError as e:
            print(f"  (role {r}: {e.response['Error']['Code']})")
    try:
        ddb.delete_table(TableName=table)
        ddb.get_waiter("table_not_exists").wait(TableName=table)
        print(f"  deleted table {table}")
    except ClientError as e:
        print(f"  (table {table}: {e.response['Error']['Code']})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--suffix", default=None,
                    help="resource name suffix (default: derived from time-free token)")
    ap.add_argument("--keep", action="store_true", help="skip teardown")
    ap.add_argument("--teardown-only", action="store_true",
                    help="only tear down (needs --suffix)")
    args = ap.parse_args()

    session = boto3.Session(region_name=args.region)
    sts = session.client("sts")
    ident = sts.get_caller_identity()
    account = ident["Account"]
    print(f"account={account}  caller={ident['Arn']}  region={args.region}")

    suffix = args.suffix or "run1"
    table = f"lk-probe-{suffix}"
    roles = {k: f"lk-probe-{suffix}-{v['alias']}" for k, v in TENANTS.items()}
    table_arn = f"arn:aws:dynamodb:{args.region}:{account}:table/{table}"

    ddb = session.client("dynamodb")
    iam = session.client("iam")

    if args.teardown_only:
        print("teardown-only:")
        teardown(ddb, iam, table, list(roles.values()))
        return 0

    print("\n[1/4] create table + GSI"); create_table(ddb, table)
    print("[2/4] seed tenants");        seed(ddb, table)
    print("[3/4] create per-tenant roles")
    for k, t in TENANTS.items():
        create_role(iam, roles[k], account, table_arn, t["alias"])

    print("[4/4] assume tenant-A role + run assertions")
    role_a_arn = f"arn:aws:iam::{account}:role/{roles['A']}"
    client_a = assumed_ddb(sts, role_a_arn, args.region)
    print("  waiting for the policy to take effect (IAM eventual consistency) ...")
    if not wait_policy_effective(client_a, table, TENANTS["A"]["ns"]):
        print("  WARNING: same-tenant query never succeeded — results may reflect")
        print("           propagation lag, not policy. Re-run or inspect with --keep.")
    rows, ok = run_assertions(client_a, table)
    print_report(rows, ok)

    if args.keep:
        print(f"--keep set: leaving resources. Clean up later with:\n"
              f"  python probe.py --region {args.region} --teardown-only --suffix {suffix}")
    else:
        print("teardown:")
        teardown(ddb, iam, table, list(roles.values()))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
