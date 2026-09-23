"""Load user configuration from a TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import fields
from datetime import date, timedelta
from pathlib import Path
from typing import Any, TypeVar

DEFAULT_CONFIG_PATH = Path("config.toml")

T = TypeVar("T")


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Read the TOML config file into a dict."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"{path} not found. Copy config.example.toml to {path} and edit it."
        )
    # utf-8-sig tolerates the BOM some Windows editors add.
    text = path.read_text(encoding="utf-8-sig")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from e


def section(cls: type[T], data: dict[str, Any] | None) -> T:
    """Build a settings dataclass from a TOML table, rejecting unknown keys."""
    data = data or {}
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"Unknown {cls.__name__} option(s): {', '.join(unknown)}")
    return cls(**data)


def resolve_posted_since(general: dict[str, Any], today: date | None = None) -> str:
    """Return the ISO date jobs must be posted on/after to be processed.

    Uses ``posted_since`` if set, otherwise ``today - lookback_days``.
    """
    explicit = general.get("posted_since")
    if explicit:
        try:
            return date.fromisoformat(explicit).isoformat()
        except ValueError as e:
            raise ConfigError(f"posted_since must be YYYY-MM-DD, got {explicit!r}") from e
    today = today or date.today()
    return (today - timedelta(days=int(general.get("lookback_days", 30)))).isoformat()
