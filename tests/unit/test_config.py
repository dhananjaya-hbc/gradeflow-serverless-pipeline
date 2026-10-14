"""Unit tests for environment variable config loading (one loader per Lambda)."""

import pytest

from gradeflow.infrastructure.config import (
    ConfigError,
    RejectConfig,
    StatsConfig,
    StoreConfig,
    TriggerConfig,
    ValidateConfig,
    load_clean_config,
    load_reject_config,
    load_stats_config,
    load_store_config,
    load_trigger_config,
    load_validate_config,
)

ARN = "arn:aws:states:ap-south-1:123456789012:stateMachine:pipeline"


def test_each_loader_reads_only_its_own_variables() -> None:
    assert load_trigger_config({"TABLE_NAME": "T", "STATE_MACHINE_ARN": ARN}) == TriggerConfig(
        "T", ARN
    )
    assert load_validate_config({"PROCESSED_BUCKET": "p", "MIN_VALID_RATIO": "0.9"}) == (
        ValidateConfig("p", 0.9)
    )
    assert load_clean_config({"PROCESSED_BUCKET": "p"}).processed_bucket == "p"
    assert load_stats_config({"PROCESSED_BUCKET": "p", "PASS_MARK": "40"}) == StatsConfig("p", 40.0)
    assert load_store_config({"TABLE_NAME": "T"}) == StoreConfig("T")
    assert load_reject_config({"TABLE_NAME": "T", "QUARANTINE_BUCKET": "q"}) == RejectConfig(
        "T", "q"
    )


def test_defaults() -> None:
    assert load_validate_config({"PROCESSED_BUCKET": "p"}).min_valid_ratio == 0.8
    assert load_stats_config({"PROCESSED_BUCKET": "p"}).pass_mark == 50.0


def test_store_does_not_need_buckets_and_clean_does_not_need_the_table() -> None:
    """Least privilege shows up in config too: a function only sees what it may use."""
    assert load_store_config({"TABLE_NAME": "T"}).table_name == "T"
    assert load_clean_config({"PROCESSED_BUCKET": "p"}).processed_bucket == "p"


def test_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", "FromEnv")
    monkeypatch.setenv("QUARANTINE_BUCKET", "quarantine-from-env")
    assert load_reject_config() == RejectConfig("FromEnv", "quarantine-from-env")


@pytest.mark.parametrize(
    ("loader", "env", "missing"),
    [
        (load_trigger_config, {"STATE_MACHINE_ARN": ARN}, "TABLE_NAME"),
        (load_trigger_config, {"TABLE_NAME": "T"}, "STATE_MACHINE_ARN"),
        (load_validate_config, {}, "PROCESSED_BUCKET"),
        (load_clean_config, {}, "PROCESSED_BUCKET"),
        (load_stats_config, {}, "PROCESSED_BUCKET"),
        (load_store_config, {}, "TABLE_NAME"),
        (load_reject_config, {"QUARANTINE_BUCKET": "q"}, "TABLE_NAME"),
        (load_reject_config, {"TABLE_NAME": "T"}, "QUARANTINE_BUCKET"),
        (load_store_config, {"TABLE_NAME": "  "}, "TABLE_NAME"),
    ],
)
def test_missing_required_value(loader, env: dict[str, str], missing: str) -> None:
    with pytest.raises(ConfigError, match=missing):
        loader(env)


@pytest.mark.parametrize("ratio", ["abc", "1.5", "-0.1"])
def test_invalid_ratio(ratio: str) -> None:
    with pytest.raises(ConfigError, match="MIN_VALID_RATIO"):
        load_validate_config({"PROCESSED_BUCKET": "p", "MIN_VALID_RATIO": ratio})


@pytest.mark.parametrize("mark", ["abc", "101", "-1"])
def test_invalid_pass_mark(mark: str) -> None:
    with pytest.raises(ConfigError, match="PASS_MARK"):
        load_stats_config({"PROCESSED_BUCKET": "p", "PASS_MARK": mark})
