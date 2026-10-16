"""Fixtures shared by the unit tests."""

import io
import json
from collections.abc import Iterator

import pytest

from gradeflow.infrastructure.observability import logger, metrics


class LogCapture:
    """What the Powertools logger wrote: one JSON object per line."""

    def __init__(self, stream: io.StringIO) -> None:
        self._stream = stream

    @property
    def text(self) -> str:
        return self._stream.getvalue()

    @property
    def entries(self) -> list[dict]:
        return [json.loads(line) for line in self.text.splitlines() if line.strip()]

    def messages(self) -> list[str]:
        return [entry["message"] for entry in self.entries]


@pytest.fixture
def logs() -> Iterator[LogCapture]:
    """Read what the handlers log. Powertools writes to the stream it was created with, so this
    swaps that stream (pytest's caplog and capsys do not see it) and puts it back afterwards."""
    handler = logger.registered_handler
    stream = io.StringIO()
    previous = handler.setStream(stream)
    logger.remove_keys(["dataset_id"])  # a key left by an earlier test must not show up here
    yield LogCapture(stream)
    logger.remove_keys(["dataset_id"])
    handler.setStream(previous)


class FakeLambdaContext:
    """The parts of the Lambda context object that Powertools reads."""

    function_name = "TestFunction"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:ap-south-1:111122223333:function:TestFunction"
    aws_request_id = "request-1"


@pytest.fixture
def lambda_context() -> FakeLambdaContext:
    return FakeLambdaContext()


@pytest.fixture(autouse=True)
def _empty_metric_queue() -> Iterator[None]:
    """Handlers queue metrics in a shared object. Start and end every test with it empty, so a
    test that calls a handler's `process` directly cannot leak counts into the next one."""
    metrics.clear_metrics()
    yield
    metrics.clear_metrics()


class RecordedMetrics:
    """The metrics queued so far and not yet published."""

    @property
    def totals(self) -> dict[str, float]:
        return {name: sum(data["Value"]) for name, data in metrics.metric_set.items()}


@pytest.fixture
def recorded() -> RecordedMetrics:
    return RecordedMetrics()
