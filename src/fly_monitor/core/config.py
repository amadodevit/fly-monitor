"""Load source definitions from TOML."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_INTERVAL = 3600  # 1h


def config_path() -> Path:
    if env := os.environ.get("FLY_MONITOR_CONFIG"):
        return Path(env)
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "fly-monitor" / "config.toml"


def data_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "fly-monitor" / "state.db"


@dataclass(frozen=True)
class SourceConfig:
    id: str
    plugin: str
    name: str
    interval: int = DEFAULT_INTERVAL
    tags: tuple[str, ...] = ()
    settings: dict[str, Any] = field(default_factory=dict)
    rule: dict[str, int] | None = None  # health rule for sample-based sources
    checks: list[dict] | None = None  # sample-based sources: Check.to_dict() each
    client: str = ""  # who it's for; the default grouping
    environment: str = ""  # e.g. prod, staging


_DURATION = re.compile(r"^(\d+)\s*([smh]?)$")


def parse_interval(value: int | str) -> int:
    if isinstance(value, int):
        return value
    match = _DURATION.match(value.strip())
    if not match:
        raise ValueError(f"bad interval {value!r}: use seconds or e.g. '30m', '1h'")
    amount, unit = int(match[1]), match[2] or "s"
    return amount * {"s": 1, "m": 60, "h": 3600}[unit]


def parse_config(data: dict[str, Any]) -> list[SourceConfig]:
    sources: list[SourceConfig] = []
    seen: set[str] = set()
    for raw in data.get("source", []):
        try:
            source_id, plugin = raw["id"], raw["plugin"]
        except KeyError as missing:
            raise ValueError(f"source is missing {missing}: {raw}") from None
        if source_id in seen:
            raise ValueError(f"duplicate source id {source_id!r}")
        seen.add(source_id)
        sources.append(
            SourceConfig(
                id=source_id,
                plugin=plugin,
                name=raw.get("name", source_id),
                interval=parse_interval(raw.get("interval", DEFAULT_INTERVAL)),
                tags=tuple(raw.get("tags", ())),
                settings=dict(raw.get("settings", {})),
                client=str(raw.get("client", "")),
                environment=str(raw.get("environment", "")),
            )
        )
    return sources


def load_config(path: Path | None = None) -> list[SourceConfig]:
    path = path or config_path()
    with path.open("rb") as fh:
        return parse_config(tomllib.load(fh))
