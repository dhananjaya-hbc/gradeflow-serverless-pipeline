"""Load runtime configuration from environment variables (set by the SAM template).

Each Lambda has its own loader and reads only the variables that function needs, so a
function that is not meant to touch DynamoDB has no table name to configure either.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MIN_VALID_RATIO = 0.8
DEFAULT_PASS_MARK = 50.0
DEFAULT_ZSCORE_LIMIT = 3.0
DEFAULT_JUMP_THRESHOLD = 30.0
DEFAULT_CONTAMINATION = 0.05
DEFAULT_MAX_ANOMALIES = 500


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
class AnomaliesConfig:
    processed_bucket: str
    zscore_limit: float
    jump_threshold: float
    contamination: float


@dataclass(frozen=True)
class ApiConfig:
    table_name: str
    raw_bucket: str  # uploads are signed for this bucket
    allowed_origin: str  # sent as Access-Control-Allow-Origin on every response


@dataclass(frozen=True)
class StoreConfig:
    table_name: str
    processed_bucket: str  # where the anomalies file is read from
    max_anomalies: int


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


def load_anomalies_config(env: Mapping[str, str] | None = None) -> AnomaliesConfig:
    env = _env(env)
    return AnomaliesConfig(
        processed_bucket=_required(env, "PROCESSED_BUCKET"),
        zscore_limit=_number(env, "ZSCORE_LIMIT", DEFAULT_ZSCORE_LIMIT, 0.5, 10.0),
        jump_threshold=_number(env, "JUMP_THRESHOLD", DEFAULT_JUMP_THRESHOLD, 1.0, 100.0),
        contamination=_number(env, "IF_CONTAMINATION", DEFAULT_CONTAMINATION, 0.001, 0.5),
    )


def load_api_config(env: Mapping[str, str] | None = None) -> ApiConfig:
    env = _env(env)
    return ApiConfig(
        table_name=_required(env, "TABLE_NAME"),
        raw_bucket=_required(env, "RAW_BUCKET"),
        allowed_origin=env.get("ALLOWED_ORIGIN", "").strip() or "*",
    )


def load_store_config(env: Mapping[str, str] | None = None) -> StoreConfig:
    env = _env(env)
    return StoreConfig(
        table_name=_required(env, "TABLE_NAME"),
        processed_bucket=_required(env, "PROCESSED_BUCKET"),
        max_anomalies=_integer(env, "MAX_ANOMALIES", DEFAULT_MAX_ANOMALIES, 1, 10_000),
    )


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


def _integer(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got '{raw}'") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}")
    return value
