"""Load runtime configuration from environment variables (set by the SAM template)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MIN_VALID_RATIO = 0.8
DEFAULT_PASS_MARK = 50.0


class ConfigError(Exception):
    """A required environment variable is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Settings passed inward to use cases and adapters."""

    table_name: str
    processed_bucket: str
    min_valid_ratio: float
    pass_mark: float


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Read config from `env` (defaults to os.environ). Fails fast on bad values."""
    env = os.environ if env is None else env
    return Config(
        table_name=_required(env, "TABLE_NAME"),
        processed_bucket=_required(env, "PROCESSED_BUCKET"),
        min_valid_ratio=_number(env, "MIN_VALID_RATIO", DEFAULT_MIN_VALID_RATIO, 0.0, 1.0),
        pass_mark=_number(env, "PASS_MARK", DEFAULT_PASS_MARK, 0.0, 100.0),
    )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = env.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got '{raw}'") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low:g} and {high:g}, got {value:g}")
    return value
