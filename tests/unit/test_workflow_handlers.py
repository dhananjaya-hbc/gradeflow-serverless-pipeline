"""Unit tests for the five workflow Lambda handlers (state in, section out), no AWS.

Payloads go through real JSON text between handlers, like Step Functions does.
"""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.unit.fakes import (
    FakeArtifacts,
    FakeQuarantine,
    FakeRepository,
    FakeStorage,
    FakeWorkflow,
)

from gradeflow.application.anomaly_step import DetectAnomaliesStep
from gradeflow.application.pipeline_state import PipelineState
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
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
from gradeflow.infrastructure.s3_artifacts import S3DatasetArtifacts
from gradeflow.infrastructure.s3_quarantine import S3Quarantine
from gradeflow.infrastructure.s3_storage import S3FileStorage

HEADER = "student_id,student_name,class,subject,term,year,score\n"
GOOD = "S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n"
GOOD_2 = "S00002,Nimal Silva,10-B,maths,T1,2025,40\n"
UPLOADED_AT = datetime(2026, 10, 14, 10, 30, tzinfo=UTC)


def over_json(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data))


class Stack:
    """All the steps wired to one set of fakes, as the Lambdas share S3 and DynamoDB."""

    def __init__(self, files: dict[str, str]) -> None:
        self.storage = FakeStorage({("raw", k): v.encode() for k, v in files.items()})
        self.artifacts = FakeArtifacts()
        self.quarantine = FakeQuarantine()
        self.repository = FakeRepository()
        self.workflow = FakeWorkflow()

    def initial_state(self, key: str) -> dict[str, Any]:
        TriggerPipeline(self.storage, self.repository, self.workflow).execute(
            TriggerRequest("ds-1", "raw", key, UPLOADED_AT)
        )
        return over_json(self.workflow.started[-1][1])


def test_chain_of_handlers_completes_a_dataset() -> None:
    stack = Stack({"uploads/ds-1/results.csv": HEADER + GOOD + GOOD_2 + GOOD_2})
    state = stack.initial_state("uploads/ds-1/results.csv")

    state["validation"] = over_json(
        validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))
    )
    assert state["validation"]["valid"] is True
    state["cleaning"] = over_json(clean_handler.process(state, CleanStep(stack.artifacts)))
    assert (state["cleaning"]["clean_rows"], state["cleaning"]["duplicates_removed"]) == (2, 1)
    state["stats"] = over_json(stats_handler.process(state, StatsStep(stack.artifacts, 50)))
    assert state["stats"]["overall"]["pass_rate"] == 50.0
    state["anomalies"] = over_json(
        anomalies_handler.process(state, DetectAnomaliesStep(stack.artifacts, 3.0, 30.0, 0.05))
    )
    result = store_handler.process(state, StoreStep(stack.repository, stack.artifacts))

    assert result == {"dataset_id": "ds-1", "status": "COMPLETED"}
    assert stack.repository.current["ds-1"].status == DatasetStatus.COMPLETED
    assert stack.repository.stats["ds-1"].student_count == 2
    assert stack.repository.current["ds-1"].anomaly_count == state["anomalies"]["total"]


def test_anomalies_handler_returns_the_anomalies_section() -> None:
    stack = Stack({"uploads/ds-1/results.csv": HEADER + GOOD + GOOD_2})
    state = stack.initial_state("uploads/ds-1/results.csv")
    state["validation"] = over_json(
        validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))
    )
    state["cleaning"] = over_json(clean_handler.process(state, CleanStep(stack.artifacts)))

    section = anomalies_handler.process(
        state, DetectAnomaliesStep(stack.artifacts, 3.0, 30.0, 0.05)
    )

    assert section == {
        "total": 0,
        "by_method": {},
        "anomalies_key": "work/ds-1/anomalies.jsonl",
        "ml_skipped": "only 2 students, at least 20 are needed",
    }
    json.dumps(section)  # must be JSON-serialisable for Step Functions


def test_validate_handler_returns_the_validation_section() -> None:
    stack = Stack({"uploads/ds-1/results.csv": HEADER + GOOD})
    state = stack.initial_state("uploads/ds-1/results.csv")

    section = validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))

    assert section == {
        "valid": True,
        "total_rows": 1,
        "valid_rows": 1,
        "invalid_rows": 0,
        "row_errors": [],
        "valid_key": "work/ds-1/valid.jsonl",
        "error_message": None,
    }
    json.dumps(section)  # must be JSON-serialisable for Step Functions


def test_validate_handler_reports_a_bad_file_without_raising() -> None:
    stack = Stack({"uploads/ds-1/broken.csv": "student_id,score\nS00001,50\n"})
    state = stack.initial_state("uploads/ds-1/broken.csv")

    section = validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))

    assert section["valid"] is False
    assert "missing required columns" in section["error_message"]


def test_fail_handler_quarantines_a_bad_file() -> None:
    stack = Stack({"uploads/ds-1/broken.csv": "student_id,score\nS00001,50\n"})
    state = stack.initial_state("uploads/ds-1/broken.csv")
    state["validation"] = over_json(
        validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))
    )

    result = fail_handler.process(state, RejectDataset(stack.quarantine, stack.repository))

    assert result["status"] == "FAILED"
    assert "missing required columns" in result["error_message"]
    assert stack.quarantine.copied == [("raw", "uploads/ds-1/broken.csv", "ds-1")]


def test_fail_handler_after_a_crash_does_not_quarantine() -> None:
    """The state machine's Catch adds `error`; the file itself may be fine."""
    stack = Stack({"uploads/ds-1/results.csv": HEADER + GOOD})
    state = stack.initial_state("uploads/ds-1/results.csv")
    state["error"] = {"Error": "States.TaskFailed", "Cause": "Lambda timed out"}

    result = fail_handler.process(state, RejectDataset(stack.quarantine, stack.repository))

    assert result["status"] == "FAILED"
    assert result["error_message"] == "processing failed (States.TaskFailed: Lambda timed out)"
    assert stack.quarantine.copied == []


def test_handlers_fail_loudly_when_an_earlier_section_is_missing() -> None:
    """A wiring mistake in the state machine must raise (and be retried/caught), not pass."""
    stack = Stack({"uploads/ds-1/results.csv": HEADER + GOOD})
    state = stack.initial_state("uploads/ds-1/results.csv")

    with pytest.raises(ValueError, match="validation"):
        clean_handler.process(state, CleanStep(stack.artifacts))

    state["validation"] = over_json(
        validate_handler.process(state, ValidateStep(stack.storage, stack.artifacts, 0.8))
    )
    with pytest.raises(ValueError, match="cleaning"):
        stats_handler.process(state, StatsStep(stack.artifacts, 50))

    state["cleaning"] = over_json(clean_handler.process(state, CleanStep(stack.artifacts)))
    with pytest.raises(ValueError, match="stats"):
        store_handler.process(state, StoreStep(stack.repository))

    # the failed attempts changed nothing: the dataset is still PROCESSING
    assert stack.repository.current["ds-1"].status == DatasetStatus.PROCESSING


def test_bad_state_is_rejected_by_parsing() -> None:
    with pytest.raises(KeyError):
        PipelineState.from_dict({"dataset_id": "ds-1"})


# --- wiring --------------------------------------------------------------------------

REGION = "ap-south-1"


@pytest.fixture(autouse=True)
def aws_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


def build_fresh(module):
    """Call a handler module's cached builder with a clean cache."""
    module._build_step.cache_clear()
    try:
        return module._build_step()
    finally:
        module._build_step.cache_clear()


def test_validate_handler_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESSED_BUCKET", "processed-bucket")
    monkeypatch.setenv("MIN_VALID_RATIO", "0.9")

    step = build_fresh(validate_handler)

    assert isinstance(step._storage, S3FileStorage)
    assert isinstance(step._artifacts, S3DatasetArtifacts)
    assert step._artifacts._bucket == "processed-bucket"
    assert step._min_valid_ratio == 0.9


def test_clean_handler_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESSED_BUCKET", "processed-bucket")

    step = build_fresh(clean_handler)

    assert isinstance(step._artifacts, S3DatasetArtifacts)
    assert step._artifacts._bucket == "processed-bucket"


def test_stats_handler_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESSED_BUCKET", "processed-bucket")
    monkeypatch.setenv("PASS_MARK", "40")

    step = build_fresh(stats_handler)

    assert step._artifacts._bucket == "processed-bucket"
    assert step._pass_mark == 40.0


def test_store_handler_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", "PipelineTable")
    monkeypatch.setenv("PROCESSED_BUCKET", "processed-bucket")
    monkeypatch.setenv("MAX_ANOMALIES", "200")

    step = build_fresh(store_handler)

    assert isinstance(step._repository, DynamoDbDatasetRepository)
    assert isinstance(step._artifacts, S3DatasetArtifacts)
    assert step._artifacts._bucket == "processed-bucket"
    assert step._max_anomalies == 200


def test_anomalies_handler_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESSED_BUCKET", "processed-bucket")
    monkeypatch.setenv("ZSCORE_LIMIT", "2.5")
    monkeypatch.setenv("JUMP_THRESHOLD", "40")
    monkeypatch.setenv("IF_CONTAMINATION", "0.1")

    step = build_fresh(anomalies_handler)

    assert isinstance(step._artifacts, S3DatasetArtifacts)
    assert step._artifacts._bucket == "processed-bucket"
    assert (step._zscore_limit, step._jump_threshold, step._contamination) == (2.5, 40.0, 0.1)


def test_fail_handler_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", "PipelineTable")
    monkeypatch.setenv("QUARANTINE_BUCKET", "quarantine-bucket")

    step = build_fresh(fail_handler)

    assert isinstance(step._quarantine, S3Quarantine)
    assert step._quarantine._bucket == "quarantine-bucket"
    assert isinstance(step._repository, DynamoDbDatasetRepository)


@pytest.mark.parametrize(
    ("module", "missing_env"),
    [
        (validate_handler, "PROCESSED_BUCKET"),
        (clean_handler, "PROCESSED_BUCKET"),
        (stats_handler, "PROCESSED_BUCKET"),
        (anomalies_handler, "PROCESSED_BUCKET"),
        (store_handler, "TABLE_NAME"),
        (fail_handler, "TABLE_NAME"),
    ],
)
def test_each_handler_fails_fast_when_its_config_is_missing(
    monkeypatch: pytest.MonkeyPatch, module, missing_env: str
) -> None:
    for name in ("PROCESSED_BUCKET", "TABLE_NAME", "QUARANTINE_BUCKET"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(Exception, match=missing_env):
        build_fresh(module)
