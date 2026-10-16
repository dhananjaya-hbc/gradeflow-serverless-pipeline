"""The shared logging and metrics module, and the rules for packaging its library."""

import io
import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from gradeflow.infrastructure.observability import (
    METRICS_NAMESPACE,
    SERVICE_NAME,
    MetricName,
    logger,
    metrics,
    record_metric,
    with_logging,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def log_lines() -> Iterator[io.StringIO]:
    """Powertools writes to the stream it was created with, so swap it to read what was logged."""
    handler = logger.registered_handler
    stream = io.StringIO()
    previous = handler.setStream(stream)
    yield stream
    handler.setStream(previous)


def parsed(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


class FakeContext:
    """The parts of the Lambda context object that Powertools reads."""

    function_name = "ValidateFunction"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:ap-south-1:111122223333:function:ValidateFunction"
    aws_request_id = "request-1"


# --- metric names ------------------------------------------------------------------------------


def test_the_metric_names_are_the_ones_the_dashboard_expects() -> None:
    """Renaming one silently empties a dashboard widget, so changing this list is deliberate."""
    assert {m.value for m in MetricName} == {
        "RowsReceived",
        "RowsRejected",
        "AnomaliesFound",
        "DatasetsCompleted",
        "DatasetsFailed",
        "DuplicatesSkipped",
        "ApiServerErrors",
    }


def test_there_are_at_most_ten_metrics_because_more_cost_money() -> None:
    """The first 10 custom metrics in an account are free; each further one is billed monthly."""
    assert len(MetricName) <= 10


def test_metric_names_are_plain_pascal_case() -> None:
    for name in MetricName:
        assert re.fullmatch(r"[A-Z][A-Za-z]+", name.value), name


# --- recording metrics -------------------------------------------------------------------------


def test_a_recorded_metric_becomes_a_cloudwatch_metric_line(capsys: pytest.CaptureFixture) -> None:
    metrics.clear_metrics()
    record_metric(MetricName.ROWS_RECEIVED, 904)
    metrics.flush_metrics()
    line = json.loads(capsys.readouterr().out)

    (group,) = line["_aws"]["CloudWatchMetrics"]
    assert group["Namespace"] == METRICS_NAMESPACE == "GradeFlow"
    assert group["Dimensions"] == [["service"]]  # one dimension only: more would mean more metrics
    assert group["Metrics"] == [{"Name": "RowsReceived", "Unit": "Count"}]
    assert line["service"] == SERVICE_NAME
    assert line["RowsReceived"] == [904.0]


def test_a_metric_counts_one_by_default(capsys: pytest.CaptureFixture) -> None:
    metrics.clear_metrics()
    record_metric(MetricName.DATASETS_COMPLETED)
    metrics.flush_metrics()
    assert json.loads(capsys.readouterr().out)["DatasetsCompleted"] == [1.0]


def test_only_names_from_the_list_are_accepted() -> None:
    metrics.clear_metrics()
    with pytest.raises(AttributeError):
        record_metric("SomethingMadeUp")  # type: ignore[arg-type]
    metrics.clear_metrics()


# --- logging -----------------------------------------------------------------------------------


def test_a_log_line_is_json_with_the_service_and_any_extra_fields(log_lines: io.StringIO) -> None:
    logger.info("dataset validated", dataset_id="ds-1", valid_rows=894, total_rows=904)
    (entry,) = parsed(log_lines)
    assert entry["message"] == "dataset validated"
    assert entry["level"] == "INFO"
    assert entry["service"] == "gradeflow"
    assert (entry["dataset_id"], entry["valid_rows"], entry["total_rows"]) == ("ds-1", 894, 904)


def test_the_handler_decorator_adds_the_lambda_context(log_lines: io.StringIO) -> None:
    @with_logging
    def handler(event: dict, context: object) -> None:
        logger.info("working")

    handler({}, FakeContext())
    (entry,) = parsed(log_lines)
    assert entry["function_name"] == "ValidateFunction"
    assert entry["function_request_id"] == "request-1"
    assert entry["function_memory_size"] == 256
    assert "cold_start" in entry


def test_a_dataset_id_does_not_leak_into_the_next_invocation(log_lines: io.StringIO) -> None:
    """A warm container serves many datasets; each log line must name only its own."""

    @with_logging
    def handler(event: dict, context: object) -> None:
        if "id" in event:
            logger.append_keys(dataset_id=event["id"])
        logger.info("working")

    handler({"id": "A"}, FakeContext())
    handler({}, FakeContext())  # this call never names a dataset
    first, second = parsed(log_lines)
    assert first["dataset_id"] == "A"
    assert "dataset_id" not in second


def test_an_exception_is_logged_with_its_stack_trace(log_lines: io.StringIO) -> None:
    try:
        raise ValueError("bad file")
    except ValueError:
        logger.exception("step crashed", dataset_id="ds-2")
    (entry,) = parsed(log_lines)
    assert entry["level"] == "ERROR"
    assert "ValueError: bad file" in entry["exception"]


# --- packaging ---------------------------------------------------------------------------------


def requirements(path: str) -> dict[str, str]:
    """name -> pinned version from a requirements file; unpinned lines would raise."""
    found = {}
    for raw in (ROOT / path).read_text().splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        name, separator, version = line.partition("==")
        assert separator, f"{path}: {line!r} is not pinned with =="
        found[name.strip().lower()] = version.strip()
    return found


def test_the_zip_lambdas_get_exactly_the_pinned_powertools_and_its_dependencies() -> None:
    assert requirements("src/requirements.txt") == {
        "aws-lambda-powertools": "3.36.0",
        "typing-extensions": "4.16.0",
        "jmespath": "1.1.0",
    }


@pytest.mark.parametrize("path", ["docker/anomalies/requirements.txt", "requirements-dev.txt"])
def test_the_container_and_the_tests_use_the_same_powertools_version(path: str) -> None:
    assert requirements(path)["aws-lambda-powertools"] == "3.36.0"
    if path.startswith("docker"):
        assert requirements(path)["typing-extensions"] == "4.16.0"
        assert requirements(path)["jmespath"] == "1.1.0"


@pytest.mark.parametrize(
    "forbidden", ["pandas", "numpy", "pyarrow", "scikit-learn", "scipy", "boto3", "botocore"]
)
def test_the_zip_requirements_never_repeat_what_the_layer_or_runtime_provides(
    forbidden: str,
) -> None:
    """Listing them would bloat every zip and shadow the layer's copy (and its binary wheels)."""
    assert forbidden not in requirements("src/requirements.txt")
