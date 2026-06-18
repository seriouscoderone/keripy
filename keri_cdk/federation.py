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
