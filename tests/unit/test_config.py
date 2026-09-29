"""Unit tests for environment variable config loading."""

import pytest

from gradeflow.infrastructure.config import Config, ConfigError, load_config

REQUIRED = {"TABLE_NAME": "PipelineTable", "PROCESSED_BUCKET": "processed-bucket"}


def test_loads_values() -> None:
    config = load_config({**REQUIRED, "MIN_VALID_RATIO": "0.9", "PASS_MARK": "40"})
    assert config == Config(
        table_name="PipelineTable",
        processed_bucket="processed-bucket",
        min_valid_ratio=0.9,
        pass_mark=40.0,
    )


def test_defaults() -> None:
    config = load_config(REQUIRED)
    assert config.min_valid_ratio == 0.8
    assert config.pass_mark == 50.0


def test_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", "FromEnv")
    monkeypatch.setenv("PROCESSED_BUCKET", "bucket-from-env")
    monkeypatch.delenv("MIN_VALID_RATIO", raising=False)
    monkeypatch.delenv("PASS_MARK", raising=False)
    config = load_config()
    assert (config.table_name, config.processed_bucket) == ("FromEnv", "bucket-from-env")


@pytest.mark.parametrize("name", ["TABLE_NAME", "PROCESSED_BUCKET"])
@pytest.mark.parametrize("value", [None, "  "])
def test_missing_required_value(name: str, value: str | None) -> None:
    env = {k: v for k, v in REQUIRED.items() if k != name}
    if value is not None:
        env[name] = value
    with pytest.raises(ConfigError, match=name):
        load_config(env)


@pytest.mark.parametrize("ratio", ["abc", "1.5", "-0.1"])
def test_invalid_ratio(ratio: str) -> None:
    with pytest.raises(ConfigError, match="MIN_VALID_RATIO"):
        load_config({**REQUIRED, "MIN_VALID_RATIO": ratio})


@pytest.mark.parametrize("mark", ["abc", "101", "-1"])
def test_invalid_pass_mark(mark: str) -> None:
    with pytest.raises(ConfigError, match="PASS_MARK"):
        load_config({**REQUIRED, "PASS_MARK": mark})
