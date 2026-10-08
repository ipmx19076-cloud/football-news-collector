"""Load and validate config.yaml into typed settings objects."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .dedup import DedupSettings

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
CONFIG_ENV_VAR = "FOOTBALL_NEWS_CONFIG"

# Keys that SourceConfig models explicitly; anything else lands in `options`.
_SOURCE_KEYS = {"name", "type", "url", "enabled", "timeout", "max_items"}


class ConfigError(ValueError):
    """Raised when config.yaml is missing required values or is malformed."""


@dataclass(frozen=True)
class NetworkSettings:
    user_agent: str
    default_timeout: float = 15.0
    max_retries: int = 2
    backoff_base: float = 2.0
    per_host_delay: float = 2.0
    max_retry_after: float = 30.0
    respect_robots: bool = True


@dataclass(frozen=True)
class SourceConfig:
    name: str
    type: str
    url: str | None = None
    enabled: bool = True
    timeout: float | None = None
    max_items: int = 50
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    network: NetworkSettings
    sources: list[SourceConfig]
    db_path: Path
    export_dir: Path
    log_dir: Path
    log_level: str
    summary_max_chars: int
    raw: dict[str, Any]
    dedup: DedupSettings = field(default_factory=DedupSettings)

    @property
    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


def _parse_source(entry: dict[str, Any], index: int) -> SourceConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"sources[{index}] must be a mapping")
    for key in ("name", "type"):
        if not entry.get(key):
            raise ConfigError(f"sources[{index}] is missing '{key}'")
    if entry["type"] == "rss" and not entry.get("url"):
        raise ConfigError(f"source '{entry['name']}' (rss) is missing 'url'")
    return SourceConfig(
        name=str(entry["name"]),
        type=str(entry["type"]).lower(),
        url=entry.get("url"),
        enabled=bool(entry.get("enabled", True)),
        timeout=float(entry["timeout"]) if entry.get("timeout") else None,
        max_items=int(entry.get("max_items", 50)),
        options={k: v for k, v in entry.items() if k not in _SOURCE_KEYS},
    )


def load_config(path: str | Path | None = None) -> Config:
    """Load config from `path`, $FOOTBALL_NEWS_CONFIG, or the packaged default."""
    config_path = Path(path or os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    net = raw.get("network", {}) or {}
    network = NetworkSettings(
        user_agent=str(raw.get("user_agent") or "football-news-collector/0.1"),
        default_timeout=float(net.get("default_timeout", 15)),
        max_retries=int(net.get("max_retries", 2)),
        backoff_base=float(net.get("backoff_base", 2.0)),
        per_host_delay=float(net.get("per_host_delay", 2.0)),
        max_retry_after=float(net.get("max_retry_after", 30)),
        respect_robots=bool(net.get("respect_robots", True)),
    )
    sources = [_parse_source(e, i) for i, e in enumerate(raw.get("sources") or [])]
    names = [s.name for s in sources]
    if len(names) != len(set(names)):
        raise ConfigError("source names must be unique")

    dd = raw.get("dedup", {}) or {}
    dedup = DedupSettings(
        window_hours=float(dd.get("window_hours", 36)),
        jaccard_threshold=float(dd.get("jaccard_threshold", 0.8)),
        ratio_threshold=float(dd.get("ratio_threshold", 0.9)),
        min_tokens=int(dd.get("min_tokens", 4)),
    )
    storage = raw.get("storage", {}) or {}
    logging_cfg = raw.get("logging", {}) or {}
    return Config(
        network=network,
        sources=sources,
        db_path=Path(storage.get("db_path", "data/football_news.db")),
        export_dir=Path(storage.get("export_dir", "data/exports")),
        log_dir=Path(logging_cfg.get("dir", "logs")),
        log_level=str(logging_cfg.get("level", "INFO")).upper(),
        summary_max_chars=int(raw.get("summary_max_chars", 280)),
        raw=raw,
        dedup=dedup,
    )
