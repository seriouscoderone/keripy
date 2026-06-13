#!/usr/bin/env python3
"""Real-AWS smoke: prove a pure-Python *zip* witness Lambda riding the prebuilt
arm64 KeriRuntimeLayer can incept -> sign -> serve an OOBI, i.e. libsodium
resolves from /opt/lib.

This is the load-bearing validation of the whole zip+layer runtime model. If
libsodium does not load, the inception (which signs the witness's own KEL) will
raise inside keri, the status call won't return a witness AID, and this probe
FAILS. No mock can answer this question — moto/DynamoDB-Local cannot exercise
the native libsodium load path on the real Lambda arm64 runtime — so this runs
against REAL AWS in us-east-1.

What it does (everything thrown-away, prefix keri-layer-smoke-<suffix>):
  1. Create a Baser DynamoDB table (PK/SK + `subdb-index` GSI), PAY_PER_REQUEST.
  2. Zip keri_cdk/layers/keri_runtime/ and publish_layer_version (arm64).
  3. Create an IAM role (trust lambda.amazonaws.com) with an inline policy:
     DynamoDB CRUD on the Baser table, Secrets Manager get/create/put on the
     keeper secret path, and CloudWatch Logs. Wait for IAM propagation.
  4. Zip the function code (witness_handler.py + bootstrap.py) and create the
     Lambda: runtime python3.14 (keripy needs >=3.14), arch arm64, handler
     witness_handler.handler, layers=[published layer], 120s / 1024 MB,
     LD_LIBRARY_PATH=/opt/lib.
  5. Invoke GET / (status) and GET /oobi (bare -> self-OOBI). Assert the status
     returns a witness AID (proves libsodium signed the inception) and the OOBI
     returns 200 + CESR (proves signing + serving).
  6. Tear EVERYTHING down (function, layer version, role+inline policy, table,
     and the keeper secret the handler created) and verify zero
     keri-layer-smoke-* leftovers.

Usage:
  AWS_PROFILE=personal python probe.py --region us-east-1
  AWS_PROFILE=personal python probe.py --region us-east-1 --keep
  AWS_PROFILE=personal python probe.py --region us-east-1 --teardown-only --suffix <suffix>
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

ACCOUNT = "117870855864"            # personal account; preflight asserts this
GSI_NAME = "subdb-index"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
LAYER_DIR = os.path.join(REPO, "keri_cdk", "layers", "keri_runtime")
WITNESS_DIR = os.path.join(REPO, "keri_cdk", "handlers", "witness")

RUNTIME = "python3.14"              # keripy setup.py pins python_requires>=3.14.0


# ── names ──────────────────────────────────────────────────────────────────
def names(suffix):
    base = f"keri-layer-smoke-{suffix}"
    return {
        "base": base,
        "table": f"{base}-db",
        "layer": f"{base}-layer",
        "role": f"{base}-role",
        "func": base,
        # keeper secret path the handler get-or-creates (matches WITNESS_KEEPER_SECRET)
        "secret": f"keri/{base}/keeper",
    }


# ── 1. Baser table ───────────────────────────────────────────────────────────
def create_table(ddb, table):
    print(f"  creating Baser table {table} ...")
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
    for _ in range(60):
        gsis = ddb.describe_table(TableName=table)["Table"].get("GlobalSecondaryIndexes", [])
        if gsis and all(g["IndexStatus"] == "ACTIVE" for g in gsis):
            break
        time.sleep(1)
    print("  table + GSI active")


# ── 2. publish the built layer ────────────────────────────────────────────────
def _zip_dir(src_dir, arcprefix=""):
    """Zip a directory tree into in-memory bytes, preserving symlinks (the
    libsodium.so.26 -> libsodium.so.26.1.0 SONAME link must survive)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for fn in files + [d for d in _dirs if os.path.islink(os.path.join(root, d))]:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, src_dir)
                arc = os.path.join(arcprefix, rel) if arcprefix else rel
                if os.path.islink(full):
                    # store the symlink as a symlink (zip external attrs)
                    zi = zipfile.ZipInfo(arc)
                    zi.create_system = 3  # unix
                    zi.external_attr = (0xA1FF << 16)  # symlink mode
                    zf.writestr(zi, os.readlink(full))
                else:
                    zf.write(full, arc)
    return buf.getvalue()


def publish_layer(lam, layer_name):
    if not os.path.isdir(os.path.join(LAYER_DIR, "python", "keri")):
        raise SystemExit(
            f"FATAL: layer asset not built ({LAYER_DIR}/python/keri missing). "
            f"Run: bash keri_cdk/layers/build_layer.sh")
    print(f"  zipping layer asset {LAYER_DIR} ...")
    payload = _zip_dir(LAYER_DIR)
    print(f"  layer zip = {len(payload) / 1e6:.1f} MB; publishing {layer_name} ...")
    resp = lam.publish_layer_version(
        LayerName=layer_name,
        Content={"ZipFile": payload},
        CompatibleRuntimes=[RUNTIME],
        CompatibleArchitectures=["arm64"],
        Description="keri-layer-smoke prebuilt arm64 runtime",
    )
    arn = resp["LayerVersionArn"]
    print(f"  published {arn}")
    return arn


# ── 3. IAM role ────────────────────────────────────────────────────────────────
def create_role(iam, role, table_arn, secret_arn_prefix, region, account):
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    print(f"  creating role {role} ...")
    iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(trust),
                    Description="keri-layer-smoke throwaway")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BaserCRUD",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
                    "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem",
                    "dynamodb:DescribeTable",
                ],
                "Resource": [table_arn, table_arn + "/index/*"],
            },
            {
                "Sid": "KeeperSecret",
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:CreateSecret",
                    "secretsmanager:PutSecretValue",
                ],
                "Resource": secret_arn_prefix,
            },
            {
                "Sid": "Logs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                           "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{region}:{account}:*",
            },
        ],
    }
    iam.put_role_policy(RoleName=role, PolicyName="smoke-inline",
                        PolicyDocument=json.dumps(policy))
    arn = iam.get_role(RoleName=role)["Role"]["Arn"]
    print(f"  role {arn}; waiting for IAM propagation ...")
    time.sleep(12)   # IAM is eventually consistent for the Lambda assume-role
    return arn


# ── 4. function ────────────────────────────────────────────────────────────────
def zip_function():
    """Zip just the two handler files at the archive root (so the handler
    `witness_handler.handler` resolves and `from bootstrap import ...` works)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in ("witness_handler.py", "bootstrap.py"):
            zf.write(os.path.join(WITNESS_DIR, fn), fn)
    return buf.getvalue()


def create_function(lam, func, role_arn, layer_arn, env):
    print(f"  creating function {func} (runtime={RUNTIME}, arch=arm64) ...")
    last = None
    for attempt in range(10):
        try:
            lam.create_function(
                FunctionName=func,
                Runtime=RUNTIME,
                Architectures=["arm64"],
                Role=role_arn,
                Handler="witness_handler.handler",
                Code={"ZipFile": zip_function()},
                Layers=[layer_arn],
                Timeout=120,
                MemorySize=1024,
                Environment={"Variables": env},
            )
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            # role not yet assumable -> InvalidParameterValueException
            if code in ("InvalidParameterValueException",) and attempt < 9:
                last = e
                print(f"    (role not ready yet: {e.response['Error']['Message'][:80]}; retry)")
                time.sleep(6)
                continue
            raise
    else:
        raise last
    lam.get_waiter("function_active_v2").wait(FunctionName=func)
    print("  function active")


def invoke(lam, func, event):
    resp = lam.invoke(FunctionName=func, Payload=json.dumps(event).encode("utf-8"))
    payload = resp["Payload"].read().decode("utf-8")
    if resp.get("FunctionError"):
        return {"_error": resp["FunctionError"], "_payload": payload}
    return json.loads(payload)


def apigw_event(path, method="GET"):
    """Minimal API-Gateway-proxy event the witness handler parses
    (event['path'], event['httpMethod'])."""
    return {
        "path": path,
        "httpMethod": method,
        "headers": {},
        "queryStringParameters": None,
        "body": None,
        "isBase64Encoded": False,
    }


# ── teardown ────────────────────────────────────────────────────────────────
def teardown(sess, n, region, *, quiet=False):
    lam = sess.client("lambda")
    iam = sess.client("iam")
    ddb = sess.client("dynamodb")
    sm = sess.client("secretsmanager")
    log = (lambda *a: None) if quiet else print

    # function
    try:
        lam.delete_function(FunctionName=n["func"])
        log(f"  deleted function {n['func']}")
    except ClientError as e:
        log(f"  (function: {e.response['Error']['Code']})")

    # all layer versions
    try:
        vers = lam.list_layer_versions(LayerName=n["layer"]).get("LayerVersions", [])
        for v in vers:
            lam.delete_layer_version(LayerName=n["layer"], VersionNumber=v["Version"])
            log(f"  deleted layer {n['layer']} v{v['Version']}")
    except ClientError as e:
        log(f"  (layer: {e.response['Error']['Code']})")

    # role + inline policy
    try:
        for pn in iam.list_role_policies(RoleName=n["role"]).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=n["role"], PolicyName=pn)
        iam.delete_role(RoleName=n["role"])
        log(f"  deleted role {n['role']}")
    except ClientError as e:
        log(f"  (role: {e.response['Error']['Code']})")

    # table
    try:
        ddb.delete_table(TableName=n["table"])
        ddb.get_waiter("table_not_exists").wait(TableName=n["table"])
        log(f"  deleted table {n['table']}")
    except ClientError as e:
        log(f"  (table: {e.response['Error']['Code']})")

    # keeper secret (created by the handler at cold start)
    try:
        sm.delete_secret(SecretId=n["secret"], ForceDeleteWithoutRecovery=True)
        log(f"  deleted secret {n['secret']}")
    except ClientError as e:
        log(f"  (secret: {e.response['Error']['Code']})")


# ── leftover scan ─────────────────────────────────────────────────────────────
def scan_leftovers(sess, region):
    lam = sess.client("lambda")
    iam = sess.client("iam")
    ddb = sess.client("dynamodb")
    sm = sess.client("secretsmanager")
    pfx = "keri-layer-smoke-"
    left = {}

    funcs = []
    p = lam.get_paginator("list_functions")
    for page in p.paginate():
        funcs += [f["FunctionName"] for f in page["Functions"]
                  if f["FunctionName"].startswith(pfx)]
    if funcs:
        left["functions"] = funcs

    layers = []
    p = lam.get_paginator("list_layers")
    for page in p.paginate():
        layers += [l["LayerName"] for l in page["Layers"]
                   if l["LayerName"].startswith(pfx)]
    if layers:
        left["layers"] = layers

    roles = []
    p = iam.get_paginator("list_roles")
    for page in p.paginate():
        roles += [r["RoleName"] for r in page["Roles"]
                  if r["RoleName"].startswith(pfx)]
    if roles:
        left["roles"] = roles

    tables, kwargs = [], {}
    while True:
        resp = ddb.list_tables(**kwargs)
        tables += [t for t in resp.get("TableNames", []) if t.startswith(pfx)]
        last = resp.get("LastEvaluatedTableName")
        if not last:
            break
        kwargs["ExclusiveStartTableName"] = last
    if tables:
        left["tables"] = tables

    secrets = []
    p = sm.get_paginator("list_secrets")
    for page in p.paginate():
        secrets += [s["Name"] for s in page.get("SecretList", [])
                    if s["Name"].startswith("keri/keri-layer-smoke-")]
    if secrets:
        left["secrets"] = secrets

    return left


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--suffix", default=None, help="resource name suffix")
    ap.add_argument("--keep", action="store_true", help="skip teardown")
    ap.add_argument("--teardown-only", action="store_true",
                    help="only tear down (needs --suffix)")
    args = ap.parse_args()

    suffix = args.suffix or time.strftime("%Y%m%d%H%M%S")
    sess = boto3.Session(region_name=args.region)

    ident = sess.client("sts").get_caller_identity()
    print(f"account={ident['Account']}  caller={ident['Arn']}  region={args.region}")
    if ident["Account"] != ACCOUNT:
        print(f"!! preflight FAILED: expected account {ACCOUNT}, got {ident['Account']}. "
              f"Refusing to run.")
        return 3

    n = names(suffix)

    if args.teardown_only:
        print(f"teardown-only (suffix={suffix}):")
        teardown(sess, n, args.region)
        left = scan_leftovers(sess, args.region)
        print(f"  leftovers: {left if left else 'NONE'}")
        return 0 if not left else 2

    lam = sess.client("lambda")
    iam = sess.client("iam")
    ddb = sess.client("dynamodb")
    account = ident["Account"]
    table_arn = f"arn:aws:dynamodb:{args.region}:{account}:table/{n['table']}"
    secret_arn_prefix = f"arn:aws:secretsmanager:{args.region}:{account}:secret:keri/{n['base']}/*"

    status_aid = None
    oobi_ok = False
    failure = None

    try:
        print(f"\n[1/5] Baser table");        create_table(ddb, n["table"])
        print(f"[2/5] publish layer");        layer_arn = publish_layer(lam, n["layer"])
        print(f"[3/5] IAM role")
        role_arn = create_role(iam, n["role"], table_arn, secret_arn_prefix,
                               args.region, account)
        print(f"[4/5] function")
        env = {
            "WITNESS_NAME": n["base"],
            "WITNESS_ALIAS": "witness",
            "WITNESS_REGION": args.region,
            "WITNESS_KEEPER_SECRET": n["secret"],
            "WITNESS_BASER_TABLE": n["table"],
            "WITNESS_URL": "",
            "LD_LIBRARY_PATH": "/opt/lib",
        }
        create_function(lam, n["func"], role_arn, layer_arn, env)

        print(f"[5/5] invoke (status, then OOBI)")
        # (a) GET / -- status. The handler incepts the witness AID on cold
        #     start; a returned witness AID proves libsodium signed the
        #     inception event.
        st = invoke(lam, n["func"], apigw_event("/", "GET"))
        print(f"  status response: {json.dumps(st)[:400]}")
        if "_error" in st:
            failure = f"status invocation errored: {st['_error']} :: {st['_payload'][:600]}"
        else:
            body = st.get("body")
            sc = st.get("statusCode")
            doc = json.loads(body) if isinstance(body, str) else (body or {})
            status_aid = doc.get("witness")
            if sc != 200 or not status_aid:
                failure = failure or f"status not 200/witness-AID: sc={sc} body={body!r}"

        # (b) GET /oobi -- bare self-OOBI. 200 + CESR proves signing + serving.
        if not failure:
            ob = invoke(lam, n["func"], apigw_event("/oobi", "GET"))
            shown = json.dumps(ob)[:400] if "_error" in ob else \
                {k: (v[:120] + "..." if isinstance(v, str) and len(v) > 120 else v)
                 for k, v in ob.items()}
            print(f"  oobi response: {shown}")
            if "_error" in ob:
                failure = f"oobi invocation errored: {ob['_error']} :: {ob['_payload'][:600]}"
            else:
                sc = ob.get("statusCode")
                ctype = (ob.get("headers") or {}).get("Content-Type", "")
                obody = ob.get("body") or ""
                # CESR qb64 stream: starts with a version string / count code.
                looks_cesr = ctype == "application/cesr" or obody[:1] in ("{", "-")
                if sc == 200 and obody and looks_cesr:
                    oobi_ok = True
                else:
                    failure = f"oobi not 200/CESR: sc={sc} ctype={ctype!r} body[:80]={obody[:80]!r}"

    except Exception as e:  # noqa: BLE001
        failure = f"{type(e).__name__}: {e}"
        import traceback
        traceback.print_exc()
    finally:
        if args.keep:
            print(f"\n--keep set: leaving resources (suffix={suffix}). Clean up with:\n"
                  f"  python probe.py --region {args.region} --teardown-only --suffix {suffix}")
        else:
            print("\nteardown:")
            teardown(sess, n, args.region)

    # ── verdict ──
    print("\n" + "=" * 78)
    print("ZIP+LAYER WITNESS SMOKE -- VERDICT")
    print("=" * 78)
    print(f"  witness AID returned (libsodium signed inception): "
          f"{status_aid if status_aid else 'NO'}")
    print(f"  OOBI served 200 + CESR (signing + serving)       : "
          f"{'YES' if oobi_ok else 'NO'}")

    leftovers = None
    if not args.keep:
        leftovers = scan_leftovers(sess, args.region)
        print(f"  AWS leftovers (keri-layer-smoke-*)               : "
              f"{leftovers if leftovers else 'NONE'}")

    if failure:
        print(f"\nVERDICT: FAIL -- {failure}")
        print("  -> libsodium/import or signing path is the thing to debug "
              "(.so placement in lib/ -> /opt/lib, LD_LIBRARY_PATH).")
        print("=" * 78)
        return 1
    if leftovers:
        print(f"\nVERDICT: smoke PASSED but {leftovers} leftover(s) remain -- investigate.")
        print("=" * 78)
        return 2

    print("\nVERDICT: PASS -- pure-Python zip witness on the prebuilt arm64 "
          "KeriRuntimeLayer incepted (signed via libsodium from /opt/lib), "
          "returned its witness AID, and served a 200 CESR OOBI. The zip+layer "
          "runtime model is proven on real AWS.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
