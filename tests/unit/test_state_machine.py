"""Tests for statemachine/pipeline.asl.json: its structure, and its behaviour when the
definition is run (with the ASL emulator) against the real handlers on in-memory fakes."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.unit.asl_emulator import Emulator, Execution, StatesRuntimeError
from tests.unit.fakes import (
    FakeArtifacts,
    FakeQuarantine,
    FakeRepository,
    FakeStorage,
    FakeWorkflow,
)

from gradeflow.application.anomaly_step import DetectAnomaliesStep
from gradeflow.application.pipeline_steps import (
    CleanStep,
    RejectDataset,
    StatsStep,
    StoreStep,
    TriggerPipeline,
    TriggerRequest,
    ValidateStep,
)
from gradeflow.domain.dataset import DatasetStatus
from gradeflow.entrypoints import (
    anomalies_handler,
    clean_handler,
    fail_handler,
    stats_handler,
    store_handler,
    validate_handler,
)

ASL_PATH = Path(__file__).resolve().parents[2] / "statemachine" / "pipeline.asl.json"
DEFINITION_TEXT = ASL_PATH.read_text()
DEFINITION = json.loads(DEFINITION_TEXT)
STATES = DEFINITION["States"]

HEADER = "student_id,student_name,class,subject,term,year,score\n"
GOOD = "S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n"
GOOD_2 = "S00002,Nimal Silva,10-B,maths,T1,2025,40\n"
UPLOADED_AT = datetime(2026, 10, 14, 10, 30, tzinfo=UTC)


# --- static structure ----------------------------------------------------------------


def targets(spec: dict[str, Any]) -> list[str]:
    found = [spec["Next"]] if "Next" in spec else []
    found += [rule["Next"] for rule in spec.get("Choices", [])]
    found += [spec["Default"]] if "Default" in spec else []
    found += [rule["Next"] for rule in spec.get("Catch", [])]
    return found


def test_every_transition_points_at_an_existing_state() -> None:
    assert DEFINITION["StartAt"] in STATES
    for name, spec in STATES.items():
        for target in targets(spec):
            assert target in STATES, f"{name} -> {target} does not exist"


def test_every_state_is_reachable() -> None:
    reachable, queue = set(), [DEFINITION["StartAt"]]
    while queue:
        name = queue.pop()
        if name not in reachable:
            reachable.add(name)
            queue.extend(targets(STATES[name]))
    assert reachable == set(STATES)


def test_every_state_goes_somewhere_or_ends() -> None:
    for name, spec in STATES.items():
        if spec["Type"] in {"Fail", "Succeed"}:
            continue
        has_exit = "Next" in spec or spec.get("End") or spec["Type"] == "Choice"
        assert has_exit, f"{name} has no Next/End and would stall"


LAMBDA_TASKS = [
    name
    for name, spec in STATES.items()
    if spec["Type"] == "Task"
    and spec["Resource"].startswith("${")
    and "Function" in spec["Resource"]
]


@pytest.mark.parametrize("name", LAMBDA_TASKS)
def test_every_lambda_task_retries_and_catches(name: str) -> None:
    spec = STATES[name]
    retry = spec["Retry"][0]
    assert 1 <= retry["MaxAttempts"] <= 5
    assert retry["BackoffRate"] > 1  # exponential backoff
    assert "States.TaskFailed" in retry["ErrorEquals"]  # a crash inside the Lambda is retried
    assert any("States.ALL" in rule["ErrorEquals"] for rule in spec["Catch"])


def test_all_catches_lead_to_the_failure_branch() -> None:
    for name in ("Validate", "Clean", "Stats", "Anomalies", "Store"):
        assert [rule["Next"] for rule in STATES[name]["Catch"]] == ["MarkFailed"]
        assert STATES[name]["Catch"][0]["ResultPath"] == "$.error"  # tells Reject: a crash


def test_bad_files_take_the_choice_default_into_the_failure_branch() -> None:
    assert STATES["IsValid"]["Default"] == "MarkFailed"


def test_workflow_ends_in_a_failed_execution_after_the_alert() -> None:
    assert STATES["NotifyFailure"]["Next"] == "PipelineFailed"
    assert STATES["PipelineFailed"]["Type"] == "Fail"
    assert STATES["Store"]["End"] is True


# --- behaviour: the definition run against the real handlers --------------------------


class Run:
    """One workflow execution on fakes. `broken` makes a Lambda raise `times` times."""

    def __init__(
        self,
        content: str,
        key: str = "uploads/ds-1/results.csv",
        broken: dict[str, int | None] | None = None,
        sns_error: Exception | None = None,
    ) -> None:
        self.storage = FakeStorage({("raw", key): content.encode()})
        self.artifacts = FakeArtifacts()
        self.quarantine = FakeQuarantine()
        self.repository = FakeRepository()
        workflow = FakeWorkflow()
        TriggerPipeline(self.storage, self.repository, workflow).execute(
            TriggerRequest("ds-1", "raw", key, UPLOADED_AT)
        )
        self.start_state = workflow.started[0][1]
        self.calls: dict[str, int] = {}
        self._broken = broken or {}
        lambdas: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "fn:validate": self._lambda(
                "fn:validate",
                lambda e: validate_handler.process(
                    e, ValidateStep(self.storage, self.artifacts, 0.8)
                ),
            ),
            "fn:clean": self._lambda(
                "fn:clean", lambda e: clean_handler.process(e, CleanStep(self.artifacts))
            ),
            "fn:stats": self._lambda(
                "fn:stats", lambda e: stats_handler.process(e, StatsStep(self.artifacts, 50))
            ),
            "fn:anomalies": self._lambda(
                "fn:anomalies",
                lambda e: anomalies_handler.process(
                    e, DetectAnomaliesStep(self.artifacts, 3.0, 30.0, 0.05)
                ),
            ),
            "fn:store": self._lambda(
                "fn:store",
                lambda e: store_handler.process(e, StoreStep(self.repository, self.artifacts)),
            ),
            "fn:fail": self._lambda(
                "fn:fail",
                lambda e: fail_handler.process(e, RejectDataset(self.quarantine, self.repository)),
            ),
        }
        self.execution: Execution = Emulator(DEFINITION_TEXT, lambdas, sns_error).run(
            self.start_state
        )

    def _lambda(self, name: str, real: Callable[[dict], dict]) -> Callable[[dict], dict]:
        def call(event: dict[str, Any]) -> dict[str, Any]:
            self.calls[name] = self.calls.get(name, 0) + 1
            failures = self._broken.get(name, 0)
            if name in self._broken and (failures is None or self.calls[name] <= failures):
                raise RuntimeError(f"{name} is broken")
            return real(event)

        return call

    @property
    def meta(self):
        return self.repository.current["ds-1"]


def test_happy_path_runs_every_step_and_completes_the_dataset() -> None:
    run = Run(HEADER + GOOD + GOOD_2)

    assert run.execution.status == "SUCCEEDED"
    assert run.execution.visited == [
        "Validate",
        "IsValid",
        "Clean",
        "Stats",
        "Anomalies",
        "Store",
    ]
    assert run.meta.status == DatasetStatus.COMPLETED
    assert run.meta.clean_rows == 2
    assert run.execution.published == []  # no alert for a good file
    assert run.execution.state["result"] == {"dataset_id": "ds-1", "status": "COMPLETED"}


def test_state_accumulates_each_steps_result() -> None:
    state = Run(HEADER + GOOD + GOOD_2).execution.state

    assert set(state) >= {"validation", "cleaning", "stats", "anomalies", "result"}
    assert state["validation"]["valid"] is True
    assert state["cleaning"]["processed_key"] == "processed/ds-1/data.parquet"
    assert state["stats"]["overall"]["count"] == 2
    assert state["anomalies"]["anomalies_key"] == "work/ds-1/anomalies.jsonl"


def test_bad_file_skips_clean_and_stats_and_alerts() -> None:
    run = Run("student_id,score\nS00001,50\n")

    assert run.execution.status == "FAILED"
    assert run.execution.visited == [
        "Validate",
        "IsValid",
        "MarkFailed",
        "NotifyFailure",
        "PipelineFailed",
    ]
    assert run.meta.status == DatasetStatus.FAILED
    assert run.quarantine.copied == [("raw", "uploads/ds-1/results.csv", "ds-1")]
    assert run.calls.get("fn:clean") is None and run.calls.get("fn:stats") is None
    assert run.calls.get("fn:anomalies") is None
    [alert] = run.execution.published
    assert alert["TopicArn"] == "topic:alerts"
    assert alert["Subject"] == "GradeFlow: dataset failed"
    assert "ds-1" in alert["Message"] and "missing required columns" in alert["Message"]


def test_a_transient_failure_is_retried_and_the_run_still_succeeds() -> None:
    run = Run(HEADER + GOOD + GOOD_2, broken={"fn:stats": 2})  # fails twice, then works

    assert run.execution.status == "SUCCEEDED"
    assert run.execution.attempts["Stats"] == 3
    assert run.execution.visited.count("Stats") == 1  # retries stay inside the one state
    assert run.meta.status == DatasetStatus.COMPLETED
    assert run.execution.published == []


def test_a_persistent_failure_exhausts_retries_then_takes_the_failure_branch() -> None:
    run = Run(HEADER + GOOD + GOOD_2, broken={"fn:clean": None})  # never works

    assert run.execution.attempts["Clean"] == 4  # the first try + 3 retries
    assert run.execution.status == "FAILED"
    assert run.execution.visited == [
        "Validate",
        "IsValid",
        "Clean",
        "MarkFailed",
        "NotifyFailure",
        "PipelineFailed",
    ]
    assert run.meta.status == DatasetStatus.FAILED
    assert (
        "RuntimeError" in run.meta.error_message and "fn:clean is broken" in run.meta.error_message
    )
    assert run.quarantine.copied == []  # the file was fine; only processing broke
    assert "Stats" not in run.execution.visited
    assert "fn:clean is broken" in run.execution.published[0]["Message"]


def test_a_crash_in_the_first_step_is_handled_without_row_counts() -> None:
    run = Run(HEADER + GOOD, broken={"fn:validate": None})

    assert run.execution.status == "FAILED"
    assert "validation" not in run.execution.state
    assert run.meta.status == DatasetStatus.FAILED
    assert run.meta.total_rows == 0
    assert run.quarantine.copied == []


def test_if_the_failure_cannot_be_recorded_the_alert_still_goes_out() -> None:
    run = Run("student_id,score\nS00001,50\n", broken={"fn:fail": None})

    assert run.execution.attempts["MarkFailed"] == 4
    assert run.execution.visited[-4:] == [
        "MarkFailed",
        "FailureNotRecorded",
        "NotifyFailure",
        "PipelineFailed",
    ]
    assert run.execution.status == "FAILED"
    [alert] = run.execution.published
    assert "could not be recorded" in alert["Message"]


def test_a_failing_alert_does_not_hide_the_failure() -> None:
    run = Run("student_id,score\nS00001,50\n", sns_error=RuntimeError("SNS is down"))

    assert run.execution.status == "FAILED"
    assert run.execution.visited[-2:] == ["NotifyFailure", "PipelineFailed"]
    assert run.meta.status == DatasetStatus.FAILED  # recorded before the alert was attempted


def test_a_transient_anomalies_failure_is_retried() -> None:
    run = Run(HEADER + GOOD + GOOD_2, broken={"fn:anomalies": 3})  # fails 3 times, then works

    assert run.execution.status == "SUCCEEDED"
    assert run.execution.attempts["Anomalies"] == 4  # the first try plus all 3 retries
    assert run.meta.status == DatasetStatus.COMPLETED


def test_if_the_anomalies_step_keeps_failing_the_dataset_fails_without_quarantine() -> None:
    run = Run(HEADER + GOOD + GOOD_2, broken={"fn:anomalies": None})

    assert run.execution.status == "FAILED"
    assert run.execution.visited == [
        "Validate",
        "IsValid",
        "Clean",
        "Stats",
        "Anomalies",
        "MarkFailed",
        "NotifyFailure",
        "PipelineFailed",
    ]
    assert run.meta.status == DatasetStatus.FAILED
    assert "fn:anomalies is broken" in run.meta.error_message
    assert run.quarantine.copied == []  # the file was fine; only the detection broke
    assert "Store" not in run.execution.visited  # nothing half-written: no stats, no anomalies
    assert run.repository.stats == {} and run.repository.anomalies == {}


def test_a_completed_run_stores_its_anomalies_and_their_count() -> None:
    run = Run(HEADER + GOOD + GOOD_2)

    assert run.meta.anomaly_count == run.execution.state["anomalies"]["total"]
    assert run.meta.anomalies_stored == len(run.repository.anomalies["ds-1"])
    assert run.meta.ml_skipped == "only 2 students, at least 20 are needed"  # a tiny file


def test_the_state_machine_cannot_skip_the_anomalies_step() -> None:
    """Store requires the section, so a definition that bypassed Anomalies would fail loudly."""
    assert STATES["Stats"]["Next"] == "Anomalies"
    assert STATES["Anomalies"]["Next"] == "Store"
    assert STATES["Anomalies"]["ResultPath"] == "$.anomalies"


def test_the_alert_message_path_exists_on_every_route_to_it() -> None:
    """NotifyFailure reads $.failure.error_message; a missing path would be an uncatchable
    States.Runtime error, so every route into NotifyFailure must set $.failure first."""
    for run in (
        Run("student_id,score\nS00001,50\n"),  # bad file
        Run(HEADER + GOOD, broken={"fn:clean": None}),  # crash
        Run("student_id,score\nS00001,50\n", broken={"fn:fail": None}),  # failure not recorded
    ):
        assert "error_message" in run.execution.state["failure"]


def test_the_emulator_reports_a_missing_path_like_states_runtime() -> None:
    """Guard for the guard above: the emulator really does fail on a missing path."""
    emulator = Emulator(
        DEFINITION_TEXT,
        {
            name: (lambda e: {})
            for name in (
                "fn:validate",
                "fn:clean",
                "fn:stats",
                "fn:anomalies",
                "fn:store",
                "fn:fail",
            )
        },
    )
    with pytest.raises(StatesRuntimeError):
        emulator.run({"dataset_id": "ds-1", "key": "k"})  # Validate's result lacks .valid
