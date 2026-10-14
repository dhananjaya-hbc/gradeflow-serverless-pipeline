"""Unit tests for the state that travels through the workflow."""

import json

from gradeflow.application.pipeline_state import CleaningOutcome, PipelineState, ValidationOutcome

BASE = {
    "dataset_id": "ds-1",
    "bucket": "raw",
    "key": "uploads/ds-1/my file.csv",
    "uploaded_at": "2026-10-14T10:30:00.123000+00:00",
    "file_hash": "a" * 64,
}


def test_minimal_state_round_trip() -> None:
    state = PipelineState.from_dict(BASE)

    assert state.validation is None and state.cleaning is None
    assert state.stats is None and state.error is None
    assert state.to_dict() == BASE  # sections that have not run are left out


def test_full_state_round_trip_through_json() -> None:
    state = PipelineState(
        **BASE,
        validation=ValidationOutcome(
            valid=True,
            total_rows=10,
            valid_rows=9,
            invalid_rows=1,
            row_errors=("row 3: missing term",),
            valid_key="work/ds-1/valid.jsonl",
        ),
        cleaning=CleaningOutcome(
            clean_rows=8, duplicates_removed=1, processed_key="p/ds-1.parquet"
        ),
        stats={"overall": {"count": 8}},
        error={"Error": "States.TaskFailed", "Cause": "boom"},
    )

    restored = PipelineState.from_dict(json.loads(json.dumps(state.to_dict())))

    assert restored == state
    assert isinstance(restored.validation.row_errors, tuple)


def test_derived_fields() -> None:
    state = PipelineState.from_dict(BASE)
    assert state.filename == "my file.csv"
    assert state.uploaded_at_datetime.year == 2026
    assert state.uploaded_at_datetime.tzinfo is not None


def test_validation_outcome_defaults_for_a_bad_file() -> None:
    outcome = ValidationOutcome(valid=False, error_message="file contains no data rows")
    assert outcome.to_dict() == {
        "valid": False,
        "total_rows": 0,
        "valid_rows": 0,
        "invalid_rows": 0,
        "row_errors": [],
        "valid_key": None,
        "error_message": "file contains no data rows",
    }
