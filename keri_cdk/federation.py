"""Federation helpers for the keri.host ecosystem CDK app.

load_federation resolves the list of (slug, domain, hosted_zone_id) entries without
committing private Route53 zone IDs. build_federation (Task 2) maps that list to one
KeriCoreStack plus a witness+mailbox stack pair per entry.
"""
import json
import os
import pathlib

REQUIRED_KEYS = ("slug", "domain", "hosted_zone_id")


def load_federation(config_dir, env=None, env_var="KERI_HOST_FEDERATION"):
    """Return the federation entries (list of dicts: slug/domain/hosted_zone_id).

    Resolution order (privacy: real zone IDs are never committed):
      1. ${env_var} — inline JSON (starts with '[') or a path to a JSON file.
      2. {config_dir}/federation.json — gitignored real config.
      3. {config_dir}/federation.example.json — committed example.com placeholders.
    """
    env = os.environ if env is None else env
    config_dir = pathlib.Path(config_dir)
    raw = env.get(env_var)
    if raw:
        text = raw if raw.lstrip().startswith("[") else pathlib.Path(raw).read_text()
    else:
        real = config_dir / "federation.json"
        src = real if real.exists() else config_dir / "federation.example.json"
        text = src.read_text()
    entries = json.loads(text)
    _validate(entries)
    return entries


def _validate(entries):
    if not isinstance(entries, list) or not entries:
        raise ValueError("federation config must be a non-empty JSON list")
    seen = set()
    for e in entries:
        missing = [k for k in REQUIRED_KEYS if not e.get(k)]
        if missing:
            raise ValueError(f"federation entry {e!r} missing keys: {missing}")
        if e["slug"] in seen:
            raise ValueError(f"duplicate slug: {e['slug']!r}")
        seen.add(e["slug"])


# Imported here (after load_federation) to avoid a circular import at package load
# time: keri_cdk/__init__.py does NOT import federation, so this is safe.
from keri_cdk import KeriCoreStack, WitnessStack, MailboxStack  # noqa: E402


def build_federation(app, entries, env, *, core_table_name="keri-core", lwa_layer_arn=None):
    """Instantiate KeriCoreStack + one witness+mailbox pair per entry.

    Stack IDs are domain-derived (Witness{slug} / Mailbox{slug}) so each node's
    namespace (<stack>:kel / :mbx) and keeper secret (keri/<stack>/keeper) stay
    stable per domain regardless of config ordering.

    Returns a dict with keys: "core", "witnesses" (slug->WitnessStack),
    "mailboxes" (slug->MailboxStack).
    """
    core = KeriCoreStack(app, "KeriHostCore", table_name=core_table_name, env=env)
    witnesses, mailboxes = {}, {}
    for e in entries:
        slug, domain, zone = e["slug"], e["domain"], e["hosted_zone_id"]
        low = slug.lower()
        wdom, mdom = f"witness.{domain}", f"mailbox.{domain}"
        witnesses[slug] = WitnessStack(
            app, f"Witness{slug}",
            name=f"witness-{low}", alias=f"witness-{low}",
            domain_name=wdom, hosted_zone_id=zone,
            witness_url=f"https://{wdom}", core_table=core.table, env=env)
        mailboxes[slug] = MailboxStack(
            app, f"Mailbox{slug}",
            name=f"mailbox-{low}", alias=f"mailbox-{low}",
            domain_name=mdom, hosted_zone_id=zone,
            mailbox_url=f"https://{mdom}", core_table=core.table,
            lwa_layer_arn=lwa_layer_arn, env=env)
    return {"core": core, "witnesses": witnesses, "mailboxes": mailboxes}
