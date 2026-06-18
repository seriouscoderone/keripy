#!/usr/bin/env python3
"""Harvest the fresh witness + mailbox AIDs of the deployed federation.

Writes federation_aids.json (gitignored), the hand-off artifact for validation
(Task 9) and the downstream publisher Task 9. Run AFTER `cdk deploy --all`.

Run:
    AWS_PROFILE=personal python harvest_aids.py
"""
import json
import pathlib
import sys
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1]))
from keri_cdk.federation import load_federation  # noqa: E402

# The witness/mailbox controller root returns clean JSON identifying the node:
#   {"witness"|"mailbox": "<AID>", "alias": ..., "sn": 0, "kevers": N}
# (The /oobi endpoint returns CESR — JSON + a -V... attachment — not parseable
# JSON, so we read the role-keyed root instead. Confirmed against the deployed
# witness_handler/mailbox_handler routes 2026-06-18.)
_SELF_PATH = "/"


def extract_aid(oobi_json, role):
    """Return the node's controller AID from its self-identifying JSON payload.

    The witness/mailbox root keys the AID by role ("witness"/"mailbox"); the
    i/aid/pre fallbacks cover a raw key-event / OOBI payload.
    """
    aid = (oobi_json.get(role) or oobi_json.get("i")
           or oobi_json.get("aid") or oobi_json.get("pre"))
    if not aid:
        raise ValueError(f"no AID field in {role} payload: {oobi_json!r}")
    return aid


def _http_fetch(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def harvest(entries, fetch=_http_fetch):
    out = {"witnesses": {}, "mailboxes": {}}
    for e in entries:
        slug, domain = e["slug"], e["domain"]
        for role, key in (("witness", "witnesses"), ("mailbox", "mailboxes")):
            base = f"https://{role}.{domain}"
            payload = fetch(base + _SELF_PATH)
            out[key][slug] = {"aid": extract_aid(payload, role), "url": base}
    return out


def main():
    entries = load_federation(_HERE)
    out = harvest(entries)
    dest = _HERE / "federation_aids.json"
    dest.write_text(json.dumps(out, indent=2) + "\n")
    n = len(out["witnesses"]) + len(out["mailboxes"])
    print(f"wrote {dest} ({n} AIDs: {len(out['witnesses'])} witnesses + "
          f"{len(out['mailboxes'])} mailboxes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
