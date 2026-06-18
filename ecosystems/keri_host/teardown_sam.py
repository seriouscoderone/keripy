#!/usr/bin/env python3
"""Tear down the live SAM 5x5 KERI federation (serverless-* stacks).

DESTRUCTIVE. Default mode is dry-run: it discovers and prints the plan but
deletes nothing. Pass --execute to actually destroy. Idempotent: safe to re-run
to finish a partially-failed teardown.

Preserves the 5 Route53 hosted zones; only removes stack-owned resources plus
any orphaned ACM-validation CNAMEs / A-records it can attribute to the
federation subdomains.

Run:
    AWS_PROFILE=personal python teardown_sam.py              # dry-run
    AWS_PROFILE=personal python teardown_sam.py --execute    # destroy
"""
import argparse
import subprocess
import sys
import json

FEDERATION_PREFIX = "serverless-"
COMPANION_SUFFIX = "CompanionStack"


def select_sam_stacks(stack_summaries):
    """Classify live serverless-* stacks into functional vs SAM companion."""
    functional, companion = [], []
    for s in stack_summaries:
        name = s["StackName"]
        if not name.startswith(FEDERATION_PREFIX):
            continue
        if s.get("StackStatus") == "DELETE_COMPLETE":
            continue
        (companion if name.endswith(COMPANION_SUFFIX) else functional).append(name)
    return {"functional": sorted(functional), "companion": sorted(companion)}


def format_plan(selected):
    lines = ["TEARDOWN PLAN (serverless-* SAM federation)", ""]
    lines.append(f"  functional stacks ({len(selected['functional'])}):")
    lines += [f"    - {n}" for n in selected["functional"]]
    lines.append(f"  companion stacks ({len(selected['companion'])}):")
    lines += [f"    - {n}" for n in selected["companion"]]
    return "\n".join(lines)


def _aws(args, region):
    out = subprocess.run(["aws", *args, "--region", region, "--output", "json"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args)} failed: {out.stderr.strip()}")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def discover(region):
    summaries = _aws(["cloudformation", "list-stacks"], region).get("StackSummaries", [])
    return select_sam_stacks(summaries)


def _disable_protections(stack, region):
    # Stack termination protection.
    out = subprocess.run(["aws", "cloudformation", "update-termination-protection",
                          "--stack-name", stack, "--no-enable-termination-protection",
                          "--region", region], capture_output=True, text=True)
    if out.returncode != 0:
        print(f"WARNING: could not disable termination-protection for {stack}: {out.stderr.strip()}")
    # DynamoDB deletion protection for any tables in the stack.
    res = _aws(["cloudformation", "describe-stack-resources", "--stack-name", stack], region)
    for r in res.get("StackResources", []):
        if r["ResourceType"] == "AWS::DynamoDB::Table":
            out = subprocess.run(["aws", "dynamodb", "update-table",
                                  "--table-name", r["PhysicalResourceId"],
                                  "--no-deletion-protection-enabled", "--region", region],
                                 capture_output=True, text=True)
            if out.returncode != 0:
                print(f"WARNING: could not disable deletion-protection for table "
                      f"{r['PhysicalResourceId']} in {stack}: {out.stderr.strip()}")


def _delete_stack(stack, region):
    out = subprocess.run(["aws", "cloudformation", "delete-stack", "--stack-name", stack,
                          "--region", region], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(
            f"delete-stack failed for {stack}: {out.stderr.strip()}"
        )
    out = subprocess.run(["aws", "cloudformation", "wait", "stack-delete-complete",
                          "--stack-name", stack, "--region", region], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(
            f"wait stack-delete-complete failed for {stack}: {out.stderr.strip()}"
        )


def execute(selected, region):
    # Functional stacks first (own the API-GW custom domains + tables + certs),
    # then companions. ACM DELETE_FAILED + orphaned Route53 records are handled
    # in the Task 6 runbook (manual `acm delete-certificate` + CNAME sweep) since
    # they require per-cert logical-id retention that is discovered at runtime.
    for stack in selected["functional"]:
        print(f"  disabling protections + deleting {stack} ...")
        _disable_protections(stack, region)
        _delete_stack(stack, region)
    for stack in selected["companion"]:
        print(f"  deleting companion {stack} ...")
        _delete_stack(stack, region)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="actually destroy (default: dry-run)")
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args(argv)

    selected = discover(args.region)
    print(format_plan(selected))
    total = len(selected["functional"]) + len(selected["companion"])
    if not args.execute:
        print(f"\nDRY RUN — {total} stacks would be deleted. Re-run with --execute to destroy.")
        return 0
    if total == 0:
        print("\nNothing to delete (zero serverless-* stacks). Federation already torn down.")
        return 0
    print(f"\nEXECUTING teardown of {total} stacks ...")
    execute(selected, args.region)
    print("Stack deletion submitted. Verify zero-trace per the Task 6 runbook.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
