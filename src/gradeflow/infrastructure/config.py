"""Load runtime configuration from environment variables (set by the SAM template)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MIN_VALID_RATIO = 0.8


class ConfigError(Exception):
    """A required environment variable is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Settings passed inward to use cases and adapters."""

    table_name: str
    min_valid_ratio: float


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Read config from `env` (defaults to os.environ). Fails fast on bad values."""
    env = os.environ if env is None else env

    table_name = env.get("TABLE_NAME", "").strip()
    if not table_name:
        raise ConfigError("TABLE_NAME is not set")

    raw_ratio = env.get("MIN_VALID_RATIO", str(DEFAULT_MIN_VALID_RATIO))
    try:
        min_valid_ratio = float(raw_ratio)
    except ValueError as exc:
        raise ConfigError(f"MIN_VALID_RATIO must be a number, got '{raw_ratio}'") from exc
    if not 0.0 <= min_valid_ratio <= 1.0:
        raise ConfigError(f"MIN_VALID_RATIO must be between 0 and 1, got {min_valid_ratio}")

    return Config(table_name=table_name, min_valid_ratio=min_valid_ratio)
