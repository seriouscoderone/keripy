"""Environment-driven configuration for a Service AID Lambda.

The keeper lives in one KMS-encrypted secret per stack (`keri/<alias>/keeper`)
holding `{salt, bran, keeper-blob}`; salt/bran are read from that secret at
runtime, so there is no separate bran secret or `-ks` keeper table.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    alias: str
    core_table: str
    keeper_secret: str = ""
    witnesses: list[str] = field(default_factory=list)
    toad: int = 0
    handler_module: str = ""
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
        alias = os.environ["SERVICEAID_ALIAS"]
        wits = [w for w in os.environ.get("SERVICEAID_WITNESSES", "").split(",") if w]
        toad_env = os.environ.get("SERVICEAID_TOAD")
        toad = int(toad_env) if toad_env else len(wits)
        # Keeper secret is convention-derived from the alias unless overridden.
        keeper_secret = os.environ.get("SERVICEAID_KEEPER_SECRET") or f"keri/{alias}/keeper"
        return cls(
            alias=alias,
            core_table=os.environ["SERVICEAID_CORE_TABLE"],
            keeper_secret=keeper_secret,
            witnesses=wits,
            toad=toad,
            handler_module=os.environ.get("SERVICEAID_HANDLER", ""),
            allowlist=[a for a in os.environ.get("SERVICEAID_ALLOWLIST", "").split(",") if a],
            required_schema=os.environ.get("SERVICEAID_REQUIRED_SCHEMA", ""),
            region=os.environ.get("SERVICEAID_REGION", "us-east-1"),
            endpoint_url=os.environ.get("SERVICEAID_ENDPOINT_URL") or None,
        )
