"""Load runtime configuration from environment variables (set by the SAM template).

Each Lambda has its own loader and reads only the variables that function needs, so a
function that is not meant to touch DynamoDB has no table name to configure either.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MIN_VALID_RATIO = 0.8
DEFAULT_PASS_MARK = 50.0


class ConfigError(Exception):
    """A required environment variable is missing or invalid."""


@dataclass(frozen=True)
class TriggerConfig:
    table_name: str
    state_machine_arn: str


@dataclass(frozen=True)
class ValidateConfig:
    processed_bucket: str  # also holds the work/ files handed to the Clean step
    min_valid_ratio: float


@dataclass(frozen=True)
class CleanConfig:
    processed_bucket: str


@dataclass(frozen=True)
class StatsConfig:
    processed_bucket: str
    pass_mark: float


@dataclass(frozen=True)
class StoreConfig:
    table_name: str


@dataclass(frozen=True)
class RejectConfig:
    table_name: str
    quarantine_bucket: str


def load_trigger_config(env: Mapping[str, str] | None = None) -> TriggerConfig:
    env = _env(env)
    return TriggerConfig(
        table_name=_required(env, "TABLE_NAME"),
        state_machine_arn=_required(env, "STATE_MACHINE_ARN"),
    )


def load_validate_config(env: Mapping[str, str] | None = None) -> ValidateConfig:
    env = _env(env)
    return ValidateConfig(
        processed_bucket=_required(env, "PROCESSED_BUCKET"),
        min_valid_ratio=_number(env, "MIN_VALID_RATIO", DEFAULT_MIN_VALID_RATIO, 0.0, 1.0),
    )


def load_clean_config(env: Mapping[str, str] | None = None) -> CleanConfig:
    return CleanConfig(processed_bucket=_required(_env(env), "PROCESSED_BUCKET"))


def load_stats_config(env: Mapping[str, str] | None = None) -> StatsConfig:
    env = _env(env)
    return StatsConfig(
        processed_bucket=_required(env, "PROCESSED_BUCKET"),
        pass_mark=_number(env, "PASS_MARK", DEFAULT_PASS_MARK, 0.0, 100.0),
    )


def load_store_config(env: Mapping[str, str] | None = None) -> StoreConfig:
    return StoreConfig(table_name=_required(_env(env), "TABLE_NAME"))


def load_reject_config(env: Mapping[str, str] | None = None) -> RejectConfig:
    env = _env(env)
    return RejectConfig(
        table_name=_required(env, "TABLE_NAME"),
        quarantine_bucket=_required(env, "QUARANTINE_BUCKET"),
    )


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


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
