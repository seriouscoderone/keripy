"""Environment-driven configuration + Secrets-Manager bran loader."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    alias: str
    core_table: str
    keeper_table: str
    witnesses: list[str] = field(default_factory=list)
    toad: int = 0
    handler_module: str = ""
    bran_secret: str = ""
    allowlist: list[str] = field(default_factory=list)
    required_schema: str = ""
    region: str = "us-east-1"
    endpoint_url: str | None = None

    @property
    def kel_namespace(self) -> str:
        return f"{self.alias}:kel"

    @property
    def tel_namespace(self) -> str:
        return f"{self.alias}:tel"

    @classmethod
    def from_env(cls) -> "Config":
        wits = [w for w in os.environ.get("SERVICEAID_WITNESSES", "").split(",") if w]
        toad_env = os.environ.get("SERVICEAID_TOAD")
        toad = int(toad_env) if toad_env else len(wits)
        return cls(
            alias=os.environ["SERVICEAID_ALIAS"],
            core_table=os.environ["SERVICEAID_CORE_TABLE"],
            keeper_table=os.environ["SERVICEAID_KEEPER_TABLE"],
            witnesses=wits,
            toad=toad,
            handler_module=os.environ.get("SERVICEAID_HANDLER", ""),
            bran_secret=os.environ.get("SERVICEAID_BRAN_SECRET", ""),
            allowlist=[a for a in os.environ.get("SERVICEAID_ALLOWLIST", "").split(",") if a],
            required_schema=os.environ.get("SERVICEAID_REQUIRED_SCHEMA", ""),
            region=os.environ.get("SERVICEAID_REGION", "us-east-1"),
            endpoint_url=os.environ.get("SERVICEAID_ENDPOINT_URL") or None,
        )


def load_bran(secret_id: str, *, region: str = "us-east-1") -> str:
    """Fetch the keeper passcode (bran) from AWS Secrets Manager.

    The bran engages keripy's at-rest keeper encryption (aeid). It exists in
    plaintext only transiently in Lambda memory. Must be >= 21 chars.
    """
    import boto3
    sm = boto3.client("secretsmanager", region_name=region)
    resp = sm.get_secret_value(SecretId=secret_id)
    bran = resp["SecretString"]
    if len(bran) < 21:
        raise ValueError("bran (keeper passcode) must be at least 21 characters")
    return bran
