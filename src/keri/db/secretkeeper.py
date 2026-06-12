# -*- encoding: utf-8 -*-
"""
keri.db.secretkeeper module

A secret-backed, in-memory Keeper: the entire (small) keystore lives in one
KMS-encrypted secret per stack. Pure storage substitution for the LMDB/DynamoDB
keeper — keripy's Keeper/Manager/aeid surface is unchanged.
"""
from __future__ import annotations


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
        """Create-or-update the secret value."""
        if self.kind == "secretsmanager":
            try:
                self._c.put_secret_value(SecretId=name, SecretString=value)
            except self._c.exceptions.ResourceNotFoundException:
                self._c.create_secret(Name=name, SecretString=value)
        else:  # ssm
            self._c.put_parameter(Name=name, Value=value, Type="SecureString",
                                  Overwrite=True)

    def get_or_create(self, name: str, mint) -> tuple[bool, str]:
        """Return (created, value). If absent, store mint() and return it; else
        return the existing value (existing always wins — never overwrites)."""
        existing = self.get(name)
        if existing is not None:
            return False, existing
        value = mint()
        self.put(name, value)
        return True, value
