"""Unit tests for environment variable config loading."""

import pytest

from gradeflow.infrastructure.config import Config, ConfigError, load_config


def test_loads_values() -> None:
    config = load_config({"TABLE_NAME": "PipelineTable", "MIN_VALID_RATIO": "0.9"})
    assert config == Config(table_name="PipelineTable", min_valid_ratio=0.9)


def test_min_valid_ratio_defaults_to_80_percent() -> None:
    assert load_config({"TABLE_NAME": "PipelineTable"}).min_valid_ratio == 0.8


def test_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", "FromEnv")
    monkeypatch.delenv("MIN_VALID_RATIO", raising=False)
    assert load_config().table_name == "FromEnv"


@pytest.mark.parametrize("env", [{}, {"TABLE_NAME": "  "}])
def test_missing_table_name(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="TABLE_NAME"):
        load_config(env)


@pytest.mark.parametrize("ratio", ["abc", "1.5", "-0.1"])
def test_invalid_ratio(ratio: str) -> None:
    with pytest.raises(ConfigError, match="MIN_VALID_RATIO"):
        load_config({"TABLE_NAME": "PipelineTable", "MIN_VALID_RATIO": ratio})
