"""Environment-driven config for a Service-AID Lambda. The keeper lives in one
KMS-encrypted Secrets Manager secret per stack (keri/<alias>/keeper)."""
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
    handler_ref: str = ""        # ASGI-style module:attr (e.g. "gated_handler:svc")
    region: str = "us-east-1"
    endpoint_url: str | None = None
    secret_endpoint_url: str | None = None
    cas_bucket: str = ""

    @property
    def kel_namespace(self) -> str:
        return f"{self.alias}:kel"

    @property
    def tel_namespace(self) -> str:
        return f"{self.alias}:tel"

    @property
    def pub_namespace(self) -> str:
        return f"{self.alias}:pub"

    @classmethod
    def from_env(cls) -> "Config":
        alias = os.environ["SERVICEAID_ALIAS"]
        wits = [w for w in os.environ.get("SERVICEAID_WITNESSES", "").split(",") if w]
        toad_env = os.environ.get("SERVICEAID_TOAD")
        toad = int(toad_env) if toad_env else len(wits)
        keeper_secret = (os.environ.get("SERVICEAID_KEEPER_SECRET")
                         or f"keri/{alias}/keeper")
        return cls(
            alias=alias,
            core_table=os.environ["SERVICEAID_CORE_TABLE"],
            keeper_secret=keeper_secret,
            witnesses=wits,
            toad=toad,
            handler_ref=os.environ.get("SERVICEAID_HANDLER", ""),
            region=os.environ.get("SERVICEAID_REGION", "us-east-1"),
            endpoint_url=os.environ.get("SERVICEAID_ENDPOINT_URL") or None,
            secret_endpoint_url=os.environ.get("SERVICEAID_SECRET_ENDPOINT_URL") or None,
            cas_bucket=os.environ.get("SERVICEAID_CAS_BUCKET", ""),
        )
