"""Unit tests for the workflow step use cases, using in-memory fakes."""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from tests.unit.fakes import (
    FakeArtifacts,
    FakeQuarantine,
    FakeRepository,
    FakeStorage,
    FakeWorkflow,
)

from gradeflow.application.pipeline_state import CleaningOutcome, PipelineState, ValidationOutcome
from gradeflow.application.pipeline_steps import (
    MAX_ERROR_MESSAGE,
    CleanStep,
    RejectDataset,
    StatsStep,
    StoreStep,
    TriggerPipeline,
    TriggerRequest,
    TriggerStatus,
    ValidateStep,
    execution_name,
)
from gradeflow.domain.dataset import DatasetStatus
from gradeflow.domain.fingerprint import sha256_hex

HEADER = "student_id,student_name,class,subject,term,year,score\n"
GOOD = "S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n"
GOOD_2 = "S00002,Nimal Silva,10-B,maths,T1,2025,40\n"  # lower-case subject gets normalised
BAD = "S00003,Kasun Fernando,10-B,Maths,T1,2025,150\n"
UPLOADED_AT = datetime(2026, 10, 14, 10, 30, tzinfo=UTC)
KEY = "uploads/ds-1/results.csv"


def make_state(content: str = "x", **sections: object) -> PipelineState:
    return PipelineState(
        dataset_id="ds-1",
        bucket="raw",
        key=KEY,
        uploaded_at=UPLOADED_AT.isoformat(),
        file_hash=sha256_hex(content.encode()),
        **sections,
    )


# --- Trigger -------------------------------------------------------------------------


class TriggerHarness:
    def __init__(self, files: dict[str, str]) -> None:
        self.storage = FakeStorage({("raw", key): text.encode() for key, text in files.items()})
        self.repository = FakeRepository()
        self.workflow = FakeWorkflow()
        self.use_case = TriggerPipeline(self.storage, self.repository, self.workflow)

    def upload(
        self, dataset_id: str = "ds-1", key: str | None = None, at: datetime = UPLOADED_AT
    ) -> TriggerStatus:
        key = key or f"uploads/{dataset_id}/results.csv"
        return self.use_case.execute(TriggerRequest(dataset_id, "raw", key, at))


def test_new_upload_starts_the_workflow_and_creates_a_processing_meta() -> None:
    content = HEADER + GOOD
    harness = TriggerHarness({"uploads/ds-1/results.csv": content})

    assert harness.upload() == TriggerStatus.STARTED

    [(name, payload)] = harness.workflow.started
    assert payload == {
        "dataset_id": "ds-1",
        "bucket": "raw",
        "key": "uploads/ds-1/results.csv",
        "uploaded_at": "2026-10-14T10:30:00+00:00",
        "file_hash": sha256_hex(content.encode()),
    }
    meta = harness.repository.current["ds-1"]
    assert meta.status == DatasetStatus.PROCESSING
    assert meta.file_hash == payload["file_hash"]
    assert harness.repository.calls == ["claim", "create_meta:PROCESSING"]


def test_identical_file_under_another_id_is_a_duplicate() -> None:
    files = {"uploads/ds-1/results.csv": HEADER + GOOD, "uploads/ds-2/copy.csv": HEADER + GOOD}
    harness = TriggerHarness(files)
    harness.upload("ds-1")

    assert harness.upload("ds-2", "uploads/ds-2/copy.csv") == TriggerStatus.DUPLICATE

    assert len(harness.workflow.started) == 1  # no second run
    duplicate = harness.repository.current["ds-2"]
    assert duplicate.status == DatasetStatus.DUPLICATE
    assert duplicate.duplicate_of == "ds-1"
    assert "ds-1" in duplicate.error_message
    assert harness.repository.current["ds-1"].status == DatasetStatus.PROCESSING  # untouched


def test_redelivered_message_does_not_start_a_second_run() -> None:
    harness = TriggerHarness({"uploads/ds-1/results.csv": HEADER + GOOD})
    harness.upload()
    harness.repository.calls.clear()

    assert harness.upload() == TriggerStatus.ALREADY_STARTED  # same S3 event time

    assert len(harness.workflow.started) == 1
    assert harness.repository.calls == ["claim"]  # nothing written


def test_later_reupload_of_the_same_bytes_reprocesses_without_regressing_status() -> None:
    """A new S3 event is a new run, but a finished dataset must not slide back to PROCESSING."""
    harness = TriggerHarness({"uploads/ds-1/results.csv": HEADER + GOOD})
    harness.upload()
    current = harness.repository.current["ds-1"]
    harness.repository.current["ds-1"] = replace(current, status=DatasetStatus.COMPLETED)

    later = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    assert harness.upload(at=later) == TriggerStatus.STARTED

    assert len(harness.workflow.started) == 2
    assert harness.repository.current["ds-1"].status == DatasetStatus.COMPLETED


def test_execution_name_is_valid_deterministic_and_unique_per_upload() -> None:
    state = make_state("a")
    assert execution_name(state) == execution_name(make_state("a"))
    assert execution_name(state).startswith("ds-1-")
    assert sha256_hex(b"a")[:12] in execution_name(state)
    later = PipelineState.from_dict({**state.to_dict(), "uploaded_at": "2026-10-15T08:00:00+00:00"})
    assert execution_name(later) != execution_name(state)


@pytest.mark.parametrize("dataset_id", ["my set", "a/b:c*d", "ünï", "x" * 200, "ds-1_ok"])
def test_execution_name_only_uses_allowed_characters(dataset_id: str) -> None:
    state = PipelineState.from_dict({**make_state().to_dict(), "dataset_id": dataset_id})
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,80}", execution_name(state))


# --- Validate ------------------------------------------------------------------------


def run_validate(
    content: str, key: str = KEY, min_valid_ratio: float = 0.8
) -> tuple[ValidationOutcome, FakeArtifacts]:
    artifacts = FakeArtifacts()
    storage = FakeStorage({("raw", key): content.encode()})
    state = PipelineState.from_dict({**make_state(content).to_dict(), "key": key})
    return ValidateStep(storage, artifacts, min_valid_ratio).execute(state), artifacts


def test_valid_file_saves_only_the_valid_rows() -> None:
    outcome, artifacts = run_validate(HEADER + GOOD * 4 + GOOD_2 * 4 + BAD + GOOD)

    assert outcome == ValidationOutcome(
        valid=True,
        total_rows=10,
        valid_rows=9,
        invalid_rows=1,
        row_errors=("row 9: score '150' must be a number 0-100",),
        valid_key="work/ds-1/valid.jsonl",
    )
    assert len(artifacts.valid_rows["work/ds-1/valid.jsonl"]) == 9


def test_ratio_exactly_at_threshold_passes() -> None:
    outcome, _ = run_validate(HEADER + GOOD * 8 + BAD * 2)
    assert outcome.valid


def test_too_many_invalid_rows_is_a_bad_file_not_an_error() -> None:
    outcome, artifacts = run_validate(HEADER + GOOD * 7 + BAD * 3)

    assert not outcome.valid
    assert outcome.error_message == "only 70.0% of rows are valid (minimum 80.0%)"
    assert (outcome.total_rows, outcome.valid_rows, outcome.invalid_rows) == (10, 7, 3)
    assert outcome.valid_key is None
    assert artifacts.valid_rows == {}


def test_no_valid_rows_is_invalid_even_with_zero_threshold() -> None:
    outcome, artifacts = run_validate(HEADER + BAD, min_valid_ratio=0.0)
    assert not outcome.valid
    assert artifacts.valid_rows == {}


@pytest.mark.parametrize(
    ("content", "key", "message"),
    [
        (HEADER, KEY, "no data rows"),
        ("student_id,score\nS00001,50\n", KEY, "missing required columns"),
        ("{broken", "uploads/ds-1/results.json", "invalid JSON"),
        ("a,b", "uploads/ds-1/results.txt", "unsupported file type"),
    ],
)
def test_file_level_problem_is_invalid_with_a_message(content: str, key: str, message: str) -> None:
    outcome, artifacts = run_validate(content, key)

    assert not outcome.valid
    assert message in outcome.error_message
    assert outcome.total_rows == 0
    assert artifacts.valid_rows == {}


def test_year_limit_comes_from_upload_time() -> None:
    """A 2026 score is valid for a 2026 upload but would be 'in the future' in 2025."""
    outcome, _ = run_validate(HEADER + GOOD.replace("2025", "2026"))
    assert outcome.valid


# --- Clean ---------------------------------------------------------------------------


def test_clean_step_cleans_saves_and_reports() -> None:
    _, artifacts = run_validate(HEADER + GOOD + GOOD_2 + GOOD_2)
    state = make_state(
        validation=ValidationOutcome(valid=True, valid_key="work/ds-1/valid.jsonl"),
    )

    outcome = CleanStep(artifacts).execute(state)

    assert outcome == CleaningOutcome(
        clean_rows=2, duplicates_removed=1, processed_key="processed/ds-1/data.parquet"
    )
    data = artifacts.clean_data["processed/ds-1/data.parquet"]
    assert list(data["subject"]) == ["Maths", "Maths"]  # 'maths' was normalised
    assert str(data["score"].dtype) == "float64"


# --- Stats ---------------------------------------------------------------------------


def make_clean_artifacts() -> FakeArtifacts:
    _, artifacts = run_validate(HEADER + GOOD + GOOD_2)
    CleanStep(artifacts).execute(
        make_state(validation=ValidationOutcome(valid=True, valid_key="work/ds-1/valid.jsonl"))
    )
    return artifacts


CLEANING = CleaningOutcome(
    clean_rows=2, duplicates_removed=0, processed_key="processed/ds-1/data.parquet"
)


def test_stats_step_returns_json_ready_stats_using_the_pass_mark() -> None:
    stats = StatsStep(make_clean_artifacts(), pass_mark=50).execute(make_state(cleaning=CLEANING))

    assert stats["overall"]["count"] == 2
    assert stats["overall"]["pass_rate"] == 50.0  # 78.5 passes, 40 fails
    assert stats["student_count"] == 2
    assert [g["subject"] for g in stats["by_subject_term"]] == ["Maths"]


def test_stats_step_pass_mark_is_configurable() -> None:
    stats = StatsStep(make_clean_artifacts(), pass_mark=30).execute(make_state(cleaning=CLEANING))
    assert stats["overall"]["pass_rate"] == 100.0


# --- Store ---------------------------------------------------------------------------


def completed_state() -> PipelineState:
    stats = StatsStep(make_clean_artifacts(), 50).execute(make_state(cleaning=CLEANING))
    return make_state(
        validation=ValidationOutcome(
            valid=True,
            total_rows=3,
            valid_rows=2,
            invalid_rows=1,
            row_errors=("row 3: missing term",),
            valid_key="work/ds-1/valid.jsonl",
        ),
        cleaning=CLEANING,
        stats=stats,
    )


def test_store_step_saves_stats_then_marks_completed_last() -> None:
    repository = FakeRepository()
    state = completed_state()

    meta = StoreStep(repository).execute(state)

    assert repository.calls == ["delete_stats", "stats", "meta:COMPLETED"]
    assert repository.current["ds-1"] == meta
    assert meta.status == DatasetStatus.COMPLETED
    assert (meta.total_rows, meta.valid_rows, meta.invalid_rows) == (3, 2, 1)
    assert meta.row_errors == ("row 3: missing term",)
    assert (meta.clean_rows, meta.duplicates_removed) == (2, 0)
    assert meta.processed_key == "processed/ds-1/data.parquet"
    assert meta.file_hash == state.file_hash
    assert meta.filename == "results.csv"
    assert repository.stats["ds-1"].overall.count == 2


def test_store_step_replaces_stats_of_an_earlier_run() -> None:
    repository = FakeRepository()
    StoreStep(repository).execute(completed_state())
    first = repository.stats["ds-1"]

    StoreStep(repository).execute(completed_state())

    assert repository.calls[-3:] == ["delete_stats", "stats", "meta:COMPLETED"]
    assert repository.stats["ds-1"] == first


# --- Reject --------------------------------------------------------------------------


def reject(state: PipelineState) -> tuple[FakeQuarantine, FakeRepository]:
    quarantine, repository = FakeQuarantine(), FakeRepository()
    RejectDataset(quarantine, repository).execute(state)
    return quarantine, repository


def test_bad_file_is_quarantined_and_marked_failed_with_its_counts() -> None:
    state = make_state(
        validation=ValidationOutcome(
            valid=False,
            total_rows=10,
            valid_rows=7,
            invalid_rows=3,
            row_errors=("row 8: missing term",),
            error_message="only 70.0% of rows are valid (minimum 80.0%)",
        )
    )

    quarantine, repository = reject(state)

    assert quarantine.copied == [("raw", KEY, "ds-1")]
    meta = repository.current["ds-1"]
    assert meta.status == DatasetStatus.FAILED
    assert meta.quarantine_key == "quarantine/ds-1/results.csv"
    assert meta.error_message == "only 70.0% of rows are valid (minimum 80.0%)"
    assert (meta.total_rows, meta.valid_rows, meta.invalid_rows) == (10, 7, 3)
    assert meta.file_hash == state.file_hash
    assert repository.calls == ["meta:FAILED"]


def test_crashed_run_is_marked_failed_without_quarantine() -> None:
    """The file may be fine; only the processing broke, so no copy goes to quarantine."""
    state = make_state(error={"Error": "ClientError", "Cause": "S3 is down"})

    quarantine, repository = reject(state)

    assert quarantine.copied == []
    meta = repository.current["ds-1"]
    assert meta.status == DatasetStatus.FAILED
    assert meta.quarantine_key is None
    assert meta.error_message == "processing failed (ClientError: S3 is down)"
    assert meta.total_rows == 0  # Validate never ran


def test_crash_after_validation_keeps_the_row_counts() -> None:
    state = make_state(
        validation=ValidationOutcome(valid=True, total_rows=10, valid_rows=9, invalid_rows=1),
        error={"Error": "States.TaskFailed", "Cause": "boom"},
    )

    quarantine, repository = reject(state)

    assert quarantine.copied == []
    assert (repository.current["ds-1"].total_rows, repository.current["ds-1"].valid_rows) == (10, 9)


def test_lambda_error_cause_is_reduced_to_its_message_without_the_stack_trace() -> None:
    cause = json.dumps(
        {
            "errorMessage": "An error occurred (NoSuchKey) when calling the GetObject operation",
            "errorType": "NoSuchKey",
            "requestId": "6582dc3e",
            "stackTrace": ['  File "/var/task/gradeflow/entrypoints/validate_handler.py", line 22'],
        }
    )
    state = make_state(error={"Error": "NoSuchKey", "Cause": cause})

    _, repository = reject(state)

    assert repository.current["ds-1"].error_message == (
        "processing failed (NoSuchKey: An error occurred (NoSuchKey) when calling the "
        "GetObject operation)"
    )


def test_json_cause_without_an_error_message_is_kept_as_is() -> None:
    state = make_state(error={"Error": "States.Timeout", "Cause": '{"code": 7}'})
    _, repository = reject(state)
    assert (
        repository.current["ds-1"].error_message
        == 'processing failed (States.Timeout: {"code": 7})'
    )


def test_long_crash_messages_are_truncated() -> None:
    state = make_state(error={"Error": "E", "Cause": "x" * 5000})
    _, repository = reject(state)
    assert len(repository.current["ds-1"].error_message) == MAX_ERROR_MESSAGE
