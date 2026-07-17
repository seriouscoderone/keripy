"""Shared EGF configuration.

`EgfConfig` is the one config surface CLI and library callers both build against
(constitution: CLI parity). `source` names either the special value `"local"` or
an `https://...` OOBI base URL; `make_resolver` wires the matching `EgfSource` and
returns a ready-to-use `EgfResolver`.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from keri_serviceaid.egf.resolver import EgfResolver
from keri_serviceaid.egf.source import HttpOobiSource, LocalDirSource


@dataclass(frozen=True)
class EgfConfig:
    source: str = "local"
    document_said: str = ""
    accept_phases: Tuple[str, ...] = ("production",)
    local_dir: Optional[Path] = None


def make_resolver(cfg: EgfConfig) -> EgfResolver:
    if cfg.source == "local":
        return EgfResolver(LocalDirSource(cfg.local_dir))
    if cfg.source.startswith("https://"):
        return EgfResolver(HttpOobiSource(cfg.source))
    raise ValueError(f"unsupported EgfConfig.source {cfg.source!r}")
