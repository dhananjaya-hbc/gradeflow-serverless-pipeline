"""What the Lambda handlers write to the log: structured events, no personal data, and crashes.

CloudWatch Logs Insights can filter any field of a JSON line, so these tests pin the event names
and fields that a query such as `filter dataset_id = "ds-1"` relies on.
"""

import json
from typing import Any

import pytest
from tests.unit.test_workflow_handlers import (
    GOOD,
    GOOD_2,
    HEADER,
    Stack,
    over_json,
)

from gradeflow.application.anomaly_step import DetectAnomaliesStep
from gradeflow.application.pipeline_steps import (
    CleanStep,
    RejectDataset,
    StatsStep,
    StoreStep,
    TriggerPipeline,
    ValidateStep,
)
from gradeflow.entrypoints import (
    anomalies_handler,
    api_handler,
    clean_handler,
    fail_handler,
    stats_handler,
    store_handler,
    trigger_handler,
    validate_handler,
)

KEY = "uploads/ds-1/results.csv"
BROKEN = "uploads/ds-1/broken.csv"


def run_to_completion(stack: Stack) -> dict[str, Any]:
    """All five steps of a good file, through the real handlers' process functions."""
    state = stack.initial_state(KEY)
    state["validation"] = over_json(
        validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))
    )
    state["cleaning"] = over_json(clean_handler.process(state, CleanStep(stack.artifacts)))
    state["stats"] = over_json(stats_handler.process(state, StatsStep(stack.artifacts, 50)))
    state["anomalies"] = over_json(
        anomalies_handler.process(state, DetectAnomaliesStep(stack.artifacts, 3.0, 30.0, 0.05))
    )
    store_handler.process(state, StoreStep(stack.repository, stack.artifacts))
    return state


def by_message(logs, message: str) -> dict:
    (entry,) = [e for e in logs.entries if e["message"] == message]
    return entry


# --- each step writes one clear event ----------------------------------------------------------


def test_a_good_run_logs_one_event_per_step_with_the_numbers(logs) -> None:
    run_to_completion(Stack({KEY: HEADER + GOOD + GOOD_2 + GOOD_2}))

    assert logs.messages() == [
        "validation finished",
        "cleaning finished",
        "statistics computed",
        "anomaly detection finished",
        "dataset stored",
    ]
    validation, cleaning, stats, anomalies, stored = logs.entries
    assert (validation["total_rows"], validation["valid_rows"], validation["invalid_rows"]) == (
        3,
        3,
        0,
    )
    assert validation["valid"] is True and validation["level"] == "INFO"
    assert (cleaning["clean_rows"], cleaning["duplicates_removed"]) == (2, 1)
    assert stats["groups"] == 1  # both rows are Maths in T1
    assert anomalies["total"] == 0
    assert anomalies["ml_skipped"] == "only 2 students, at least 20 are needed"
    assert (stored["status"], stored["anomaly_count"]) == ("COMPLETED", 0)
    assert all(entry["dataset_id"] == "ds-1" for entry in logs.entries)


def test_a_file_that_fails_validation_is_a_warning_with_the_reason(logs) -> None:
    stack = Stack({BROKEN: "student_id,score\nS00001,50\n"})
    state = stack.initial_state(BROKEN)
    state["validation"] = over_json(
        validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))
    )
    fail_handler.process(state, RejectDataset(stack.quarantine, stack.repository))

    validation = by_message(logs, "validation finished")
    assert validation["level"] == "WARNING"
    assert validation["valid"] is False
    assert "missing required columns" in validation["error_message"]
    failed = by_message(logs, "dataset failed")
    assert failed["level"] == "WARNING"
    assert failed["dataset_id"] == "ds-1"
    assert failed["quarantined"] is True
    assert "missing required columns" in failed["error_message"]


def test_a_crashed_run_is_not_reported_as_quarantined(logs) -> None:
    stack = Stack({KEY: HEADER + GOOD})
    state = stack.initial_state(KEY)
    state["error"] = {"Error": "States.TaskFailed", "Cause": "Lambda timed out"}
    fail_handler.process(state, RejectDataset(stack.quarantine, stack.repository))
    assert by_message(logs, "dataset failed")["quarantined"] is False


def test_the_trigger_logs_each_upload_and_each_skipped_key(logs) -> None:
    stack = Stack({KEY: HEADER + GOOD})
    use_case = TriggerPipeline(stack.storage, stack.repository, stack.workflow)
    body = {
        "Records": [
            record("uploads/ds-1/results.csv"),
            record("somewhere/else.csv"),
        ]
    }
    event = {"Records": [{"body": json.dumps(body)}]}

    result = trigger_handler.process_event(event, use_case)

    assert result["skipped"] == ["somewhere/else.csv"]
    processed = by_message(logs, "upload processed")
    assert (processed["dataset_id"], processed["status"]) == ("ds-1", "STARTED")
    skipped = [e for e in logs.entries if e["message"].startswith("upload skipped")]
    assert skipped[0]["key"] == "somewhere/else.csv" and skipped[0]["level"] == "WARNING"


def record(key: str) -> dict[str, Any]:
    return {
        "eventTime": "2026-10-14T10:30:00+00:00",
        "s3": {"bucket": {"name": "raw"}, "object": {"key": key}},
    }


# --- no personal data in the logs --------------------------------------------------------------


def test_no_student_name_id_or_row_content_is_ever_logged(logs) -> None:
    """Names are personal data. The logs carry counts and ids of datasets, never rows."""
    rows = "".join(
        f"S0000{n},{name},10-A,Maths,T1,2025,{score}\n"
        for n, name, score in [
            (1, "Amal Perera", 78.5),
            (2, "Nimal Silva", 40),
            (3, "Kasun Fernando", 66),
            (4, "Ishara Bandara", 71),
            (5, "Pasindu Herath", 55),
        ]
    )
    bad_row = "S12,Bad Id Person,10-A,Maths,T1,2025,90\n"  # 1 of 6 rows, above the 80% minimum
    stack = Stack({KEY: HEADER + rows + bad_row})
    run_to_completion(stack)
    broken = Stack({BROKEN: "student_id,student_name\nS00001,Amal Perera\n"})
    state = broken.initial_state(BROKEN)
    state["validation"] = over_json(
        validate_handler.process(state, ValidateStep(broken.storage, broken.artifacts, 0.8))
    )
    fail_handler.process(state, RejectDataset(broken.quarantine, broken.repository))

    assert logs.entries  # something was logged, so the checks below mean something
    for private in [
        "Amal",
        "Perera",
        "Nimal",
        "Silva",
        "Kasun",
        "Fernando",
        "Ishara",
        "Herath",
        "S00001",
        "S00005",
        "Bad Id Person",
        "S12",
    ]:
        assert private not in logs.text, private
    assert "row_errors" not in logs.text  # row messages quote the rejected values


# --- every real handler is wired to the decorator ----------------------------------------------

STEP_MODULES = [
    (validate_handler, "_build_step"),
    (clean_handler, "_build_step"),
    (stats_handler, "_build_step"),
    (anomalies_handler, "_build_step"),
    (store_handler, "_build_step"),
    (fail_handler, "_build_step"),
]


class Exploding:
    """Stands in for a step or use case whose work crashes."""

    def execute(self, *args: Any) -> None:
        raise RuntimeError("boom from the step")


@pytest.mark.parametrize(
    ("module", "builder"), STEP_MODULES, ids=lambda x: getattr(x, "__name__", x)
)
def test_a_crashing_step_leaves_a_json_error_with_the_dataset_id(
    module, builder: str, monkeypatch: pytest.MonkeyPatch, logs, lambda_context
) -> None:
    monkeypatch.setattr(module, builder, lambda: Exploding())
    state = Stack({KEY: HEADER + GOOD}).initial_state(KEY)

    with pytest.raises(RuntimeError, match="boom from the step"):  # still raised: it is retried
        module.handler(state, lambda_context)

    (entry,) = logs.entries
    assert (entry["level"], entry["message"]) == ("ERROR", "handler failed")
    assert entry["dataset_id"] == "ds-1"
    assert entry["function_name"] == "TestFunction"
    assert "boom from the step" in entry["exception"]


def test_a_crashing_trigger_names_the_upload_in_its_error(
    monkeypatch: pytest.MonkeyPatch, logs, lambda_context
) -> None:
    monkeypatch.setattr(trigger_handler, "_build_use_case", lambda: Exploding())
    event = {"Records": [{"body": json.dumps({"Records": [record(KEY)]})}]}

    with pytest.raises(RuntimeError):
        trigger_handler.handler(event, lambda_context)

    (entry,) = logs.entries
    assert (entry["message"], entry["dataset_id"]) == ("handler failed", "ds-1")


def test_every_handler_function_is_wrapped_by_the_logging_decorator() -> None:
    for module in [*(m for m, _ in STEP_MODULES), trigger_handler, api_handler]:
        assert hasattr(module.handler, "__wrapped__"), f"{module.__name__}.handler is not wrapped"
