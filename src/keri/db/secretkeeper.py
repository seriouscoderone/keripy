# -*- encoding: utf-8 -*-
"""
keri.db.secretkeeper module

A secret-backed, in-memory Keeper: the entire (small) keystore lives in one
KMS-encrypted secret per stack. Pure storage substitution for the LMDB/DynamoDB
keeper — keripy's Keeper/Manager/aeid surface is unchanged.
"""
from __future__ import annotations

import base64
import json
import zlib

_BLOB_VERSION = 1


def dumpKeeper(data: dict[str, dict[str, bytes]]) -> str:
    """Serialize the keeper dict to a compressed, base64-ascii blob.

    data: {subdb_name: {hex_key: value_bytes}}. Values are bytes (CESR);
    base64-encoded for JSON transport, then the whole doc is zlib-compressed.
    """
    payload = {"v": _BLOB_VERSION,
               "d": {sub: {k: base64.b64encode(v).decode("ascii")
                           for k, v in items.items()}
                     for sub, items in data.items()}}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(zlib.compress(raw)).decode("ascii")


def loadKeeper(blob: str | None) -> dict[str, dict[str, bytes]]:
    """Inverse of dumpKeeper. None/empty -> {} (fresh keeper)."""
    if not blob:
        return {}
    raw = zlib.decompress(base64.b64decode(blob))
    payload = json.loads(raw)
    if payload.get("v") != _BLOB_VERSION:
        raise ValueError(f"unsupported keeper blob version: {payload.get('v')}")
    return {sub: {k: base64.b64decode(v) for k, v in items.items()}
            for sub, items in payload["d"].items()}


class SecretStore:
    """Thin pluggable secret-store client. Secrets Manager by default; SSM
    Parameter Store SecureString selectable via kind='ssm' (interface-ready)."""

    def __init__(self, *, region: str = "us-east-1", endpoint_url: str | None = None,
                 kind: str = "secretsmanager", session=None):
        import boto3
        self.kind = kind
        kwa = {"region_name": region}
        if endpoint_url:
            kwa["endpoint_url"] = endpoint_url
        src = session if session is not None else boto3
        if kind == "secretsmanager":
            self._c = src.client("secretsmanager", **kwa)
        elif kind == "ssm":
            self._c = src.client("ssm", **kwa)
        else:
            raise ValueError(f"unknown secret store kind: {kind!r}")

    def get(self, name: str) -> str | None:
        """Return the secret string, or None if it does not exist."""
        if self.kind == "secretsmanager":
            try:
                return self._c.get_secret_value(SecretId=name)["SecretString"]
            except self._c.exceptions.ResourceNotFoundException:
                return None
        else:  # ssm
            try:
                return self._c.get_parameter(Name=name, WithDecryption=True
                                             )["Parameter"]["Value"]
            except self._c.exceptions.ParameterNotFound:
                return None

    def put(self, name: str, value: str) -> None:
        """Create-or-update the secret value (overwrites); do NOT use for
        get-or-create — use _create_only."""
        if self.kind == "secretsmanager":
            try:
                self._c.put_secret_value(SecretId=name, SecretString=value)
            except self._c.exceptions.ResourceNotFoundException:
                self._c.create_secret(Name=name, SecretString=value)
        else:  # ssm
            self._c.put_parameter(Name=name, Value=value, Type="SecureString",
                                  Overwrite=True)

    def _create_only(self, name: str, value: str) -> bool:
        """Create the secret only if absent; never overwrite. True if created."""
        if self.kind == "secretsmanager":
            try:
                self._c.create_secret(Name=name, SecretString=value)
                return True
            except self._c.exceptions.ResourceExistsException:
                return False
        else:  # ssm
            try:
                self._c.put_parameter(Name=name, Value=value,
                                      Type="SecureString", Overwrite=False)
                return True
            except self._c.exceptions.ParameterAlreadyExists:
                return False

    def get_or_create(self, name: str, mint) -> tuple[bool, str]:
        """Return (created, value). If absent, create (atomically, never
        overwriting); else return existing. Race-safe: a caller that loses the
        create race re-reads the winner's value."""
        existing = self.get(name)
        if existing is not None:
            return False, existing
        value = mint()
        if self._create_only(name, value):
            return True, value
        return False, self.get(name)   # lost the race — winner's value wins
