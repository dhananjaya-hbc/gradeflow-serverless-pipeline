"""What each handler counts, and when.

The counts feed the CloudWatch dashboard and alarms, so a wrong number here is a wrong chart there.
Each handler records its counts as the very last thing it does, after the work has succeeded.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from tests.unit.fakes import FakeRepository, FakeStorage, FakeWorkflow
from tests.unit.test_api_handler import BrokenReader, event, make_api
from tests.unit.test_handler_logging import KEY, record, run_to_completion
from tests.unit.test_workflow_handlers import GOOD, GOOD_2, HEADER, Stack, over_json

from gradeflow.application.pipeline_steps import (
    RejectDataset,
    StoreStep,
    TriggerPipeline,
    ValidateStep,
)
from gradeflow.entrypoints import (
    api_handler,
    fail_handler,
    store_handler,
    trigger_handler,
    validate_handler,
)
from gradeflow.infrastructure.observability import MetricName, metrics

ENTRYPOINTS = Path(__file__).resolve().parents[2] / "src" / "gradeflow" / "entrypoints"
BROKEN = "uploads/ds-1/broken.csv"


# --- Validate: rows received and rejected --------------------------------------------------------


def test_validate_counts_the_rows_it_received_and_the_ones_it_rejected(recorded) -> None:
    bad_rows = "S12,Bad Id,10-A,Maths,T1,2025,90\nS00003,No Score,10-A,Maths,T1,2025,\n"
    stack = Stack({KEY: HEADER + GOOD + GOOD_2 + GOOD_2 + GOOD + GOOD_2 + bad_rows})
    state = stack.initial_state(KEY)

    validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))

    assert recorded.totals == {"RowsReceived": 7, "RowsRejected": 2}


def test_validate_still_counts_rows_when_the_file_is_rejected_for_too_many_bad_ones(
    recorded,
) -> None:
    bad = "S12,Bad Id,10-A,Maths,T1,2025,90\n" * 3
    stack = Stack({KEY: HEADER + GOOD + bad})
    state = stack.initial_state(KEY)

    section = validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))

    assert section["valid"] is False
    assert recorded.totals == {"RowsReceived": 4, "RowsRejected": 3}


def test_validate_counts_zero_for_a_file_with_the_wrong_columns(recorded) -> None:
    stack = Stack({BROKEN: "student_id,score\nS00001,50\n"})
    state = stack.initial_state(BROKEN)

    validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))

    assert recorded.totals == {"RowsReceived": 0, "RowsRejected": 0}


# --- Store: completed datasets and the anomalies found -------------------------------------------


def test_store_counts_one_completed_dataset_and_all_the_anomalies_found(recorded) -> None:
    # the same student ID under two names is a duplicate_id finding
    clash = "S00001,Someone Else,10-A,Maths,T2,2025,60\n"
    stack = Stack({KEY: HEADER + GOOD + GOOD_2 + clash})

    state = run_to_completion(stack)

    found = state["anomalies"]["total"]
    assert found > 0  # the scenario really produced a finding
    assert recorded.totals["DatasetsCompleted"] == 1
    assert recorded.totals["AnomaliesFound"] == found
    assert stack.repository.current["ds-1"].anomaly_count == found


def test_store_counts_zero_anomalies_for_a_clean_dataset(recorded) -> None:
    run_to_completion(Stack({KEY: HEADER + GOOD + GOOD_2}))
    assert recorded.totals["DatasetsCompleted"] == 1
    assert recorded.totals["AnomaliesFound"] == 0


def test_store_counts_the_true_total_even_when_fewer_are_stored(recorded) -> None:
    """META keeps the true total when the cap hides some findings; the metric must match META."""
    stack = Stack({KEY: HEADER + GOOD + GOOD_2 + "S00001,Someone Else,10-A,Maths,T2,2025,60\n"})
    state = run_to_completion(stack)
    found = state["anomalies"]["total"]
    assert found > 0
    metrics.clear_metrics()  # forget the first run; store the same dataset again with a cap of 0

    capped = FakeRepository()
    store_handler.process(state, StoreStep(capped, stack.artifacts, max_anomalies=0))

    meta = capped.current["ds-1"]
    assert (meta.anomaly_count, meta.anomalies_stored) == (found, 0)
    assert recorded.totals["AnomaliesFound"] == found


# --- Fail: failed datasets -----------------------------------------------------------------------


def test_fail_counts_one_failed_dataset_for_a_bad_file(recorded) -> None:
    stack = Stack({BROKEN: "student_id,score\nS00001,50\n"})
    state = stack.initial_state(BROKEN)
    state["validation"] = over_json(
        validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))
    )

    fail_handler.process(state, RejectDataset(stack.quarantine, stack.repository))

    assert recorded.totals["DatasetsFailed"] == 1


def test_fail_counts_one_failed_dataset_for_a_crash_too(recorded) -> None:
    stack = Stack({KEY: HEADER + GOOD})
    state = stack.initial_state(KEY)
    state["error"] = {"Error": "States.TaskFailed", "Cause": "Lambda timed out"}

    fail_handler.process(state, RejectDataset(stack.quarantine, stack.repository))

    assert recorded.totals == {"DatasetsFailed": 1}


# --- Trigger: duplicates skipped -----------------------------------------------------------------


def trigger_event(*keys: str) -> dict[str, Any]:
    return {"Records": [{"body": json.dumps({"Records": [record(k) for k in keys]})}]}


def test_trigger_counts_an_upload_identical_to_an_earlier_one(recorded) -> None:
    same_bytes = (HEADER + GOOD).encode()
    storage = FakeStorage(
        {("raw", "uploads/ds-1/a.csv"): same_bytes, ("raw", "uploads/ds-2/b.csv"): same_bytes}
    )
    use_case = TriggerPipeline(storage, FakeRepository(), FakeWorkflow())

    result = trigger_handler.process_event(
        trigger_event("uploads/ds-1/a.csv", "uploads/ds-2/b.csv"), use_case
    )

    assert [t["status"] for t in result["triggered"]] == ["STARTED", "DUPLICATE"]
    assert recorded.totals == {"DuplicatesSkipped": 1}


def test_trigger_counts_nothing_for_a_new_file(recorded) -> None:
    storage = FakeStorage({("raw", "uploads/ds-1/a.csv"): (HEADER + GOOD).encode()})
    use_case = TriggerPipeline(storage, FakeRepository(), FakeWorkflow())

    trigger_handler.process_event(trigger_event("uploads/ds-1/a.csv"), use_case)

    assert recorded.totals == {}


# --- API: server errors --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "counted"), [(200, False), (404, False), (429, False), (500, True), (503, True)]
)
def test_the_api_counts_only_server_errors(
    status: int, counted: bool, monkeypatch: pytest.MonkeyPatch, lambda_context, capsys, logs
) -> None:
    class Stub:
        def handle(self, _event: dict) -> dict:
            return {"statusCode": status, "headers": {}, "body": "{}"}

    monkeypatch.setattr(api_handler, "_build_api", lambda: Stub())

    api_handler.handler(event("GET", "/datasets"), lambda_context)

    published = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    if counted:
        (line,) = published
        assert line["ApiServerErrors"] == [1.0]
    else:
        assert published == []


def test_a_real_unexpected_error_in_the_api_is_published_as_a_server_error(
    monkeypatch: pytest.MonkeyPatch, lambda_context, capsys, logs
) -> None:
    monkeypatch.setattr(api_handler, "_build_api", lambda: make_api(BrokenReader())[0])

    response = api_handler.handler(event("GET", "/datasets"), lambda_context)

    assert response["statusCode"] == 500
    published = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    (line,) = published
    group = line["_aws"]["CloudWatchMetrics"][0]
    assert group["Namespace"] == "GradeFlow"
    assert [m["Name"] for m in group["Metrics"]] == ["ApiServerErrors"]


# --- counts are recorded last: a step that fails records nothing ----------------------------------


class Exploding:
    """Stands in for a step or use case whose work crashes."""

    def execute(self, *args: Any) -> None:
        raise RuntimeError("boom")


def test_a_crashing_step_has_counted_nothing(recorded) -> None:
    """A crashed step is retried, so counting before the work would count the retry twice."""
    state = Stack({KEY: HEADER + GOOD}).initial_state(KEY)
    sqs_event = trigger_event(KEY)
    calls = [
        lambda: validate_handler.process(state, Exploding()),
        lambda: store_handler.process(state, Exploding()),
        lambda: fail_handler.process(state, Exploding()),
        lambda: trigger_handler.process_event(sqs_event, Exploding()),
    ]
    for call in calls:
        with pytest.raises(RuntimeError, match="boom"):
            call()
        assert recorded.totals == {}


# --- the metric list and the handlers agree ------------------------------------------------------

OWNERS = {
    MetricName.ROWS_RECEIVED: "validate_handler.py",
    MetricName.ROWS_REJECTED: "validate_handler.py",
    MetricName.ANOMALIES_FOUND: "store_handler.py",
    MetricName.DATASETS_COMPLETED: "store_handler.py",
    MetricName.DATASETS_FAILED: "fail_handler.py",
    MetricName.DUPLICATES_SKIPPED: "trigger_handler.py",
    MetricName.API_SERVER_ERRORS: "api_handler.py",
}


def test_every_metric_has_exactly_one_handler_that_records_it() -> None:
    """A metric nobody records is an empty chart; one recorded twice is a wrong number."""
    assert set(OWNERS) == set(MetricName)
    for metric, owner in OWNERS.items():
        recorders = [
            path.name
            for path in ENTRYPOINTS.glob("*_handler.py")
            if re.search(rf"record_metric\(\s*MetricName\.{metric.name}\b", path.read_text())
        ]
        assert recorders == [owner], f"{metric.value} is recorded by {recorders}, expected {owner}"


@pytest.mark.parametrize(
    "module", [validate_handler, store_handler, fail_handler, trigger_handler, api_handler]
)
def test_handlers_that_record_metrics_publish_them(module) -> None:
    """record_metric only queues; without with_metrics nothing would ever reach CloudWatch."""
    source = (ENTRYPOINTS / f"{module.__name__.rsplit('.', 1)[1]}.py").read_text()
    assert "@with_logging\n@with_metrics\ndef handler" in source
