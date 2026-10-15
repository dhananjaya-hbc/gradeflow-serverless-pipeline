"""Unit tests for environment variable config loading (one loader per Lambda)."""

import pytest

from gradeflow.infrastructure.config import (
    AnomaliesConfig,
    ConfigError,
    RejectConfig,
    StatsConfig,
    StoreConfig,
    TriggerConfig,
    ValidateConfig,
    load_anomalies_config,
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


# --- anomalies ----------------------------------------------------------------------


def test_anomalies_config_values_and_defaults() -> None:
    assert load_anomalies_config({"PROCESSED_BUCKET": "p"}) == AnomaliesConfig("p", 3.0, 30.0, 0.05)
    custom = {
        "PROCESSED_BUCKET": "p",
        "ZSCORE_LIMIT": "2.5",
        "JUMP_THRESHOLD": "40",
        "IF_CONTAMINATION": "0.1",
    }
    assert load_anomalies_config(custom) == AnomaliesConfig("p", 2.5, 40.0, 0.1)


def test_anomalies_config_needs_the_processed_bucket() -> None:
    with pytest.raises(ConfigError, match="PROCESSED_BUCKET"):
        load_anomalies_config({})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ZSCORE_LIMIT", "0.1"), ("ZSCORE_LIMIT", "50"), ("ZSCORE_LIMIT", "abc"),
        ("JUMP_THRESHOLD", "0"), ("JUMP_THRESHOLD", "150"), ("JUMP_THRESHOLD", "abc"),
        ("IF_CONTAMINATION", "0"), ("IF_CONTAMINATION", "0.6"), ("IF_CONTAMINATION", "abc"),
    ],
)  # fmt: skip
def test_anomalies_config_rejects_values_that_make_no_sense(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_anomalies_config({"PROCESSED_BUCKET": "p", name: value})


def test_store_config_reads_the_anomaly_settings() -> None:
    config = load_store_config({"TABLE_NAME": "T", "PROCESSED_BUCKET": "p", "MAX_ANOMALIES": "200"})
    assert config == StoreConfig("T", "p", 200)


def test_store_config_defaults_to_500_anomalies() -> None:
    assert load_store_config({"TABLE_NAME": "T"}).max_anomalies == 500


@pytest.mark.parametrize("value", ["0", "10001", "abc", "1.5", "-3"])
def test_store_config_rejects_a_bad_anomaly_cap(value: str) -> None:
    with pytest.raises(ConfigError, match="MAX_ANOMALIES"):
        load_store_config({"TABLE_NAME": "T", "MAX_ANOMALIES": value})
