"""Unit tests for the state that travels through the workflow."""

import json

import pytest

from gradeflow.application.pipeline_state import (
    AnomalyOutcome,
    CleaningOutcome,
    PipelineState,
    ValidationOutcome,
    require,
)

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
        anomalies=AnomalyOutcome(
            total=6,
            by_method={"term_jump": 5, "duplicate_id": 1},
            anomalies_key="work/ds-1/anomalies.jsonl",
            ml_skipped="only 3 students, at least 20 are needed",
        ),
        error={"Error": "States.TaskFailed", "Cause": "boom"},
    )

    restored = PipelineState.from_dict(json.loads(json.dumps(state.to_dict())))

    assert restored == state
    assert isinstance(restored.validation.row_errors, tuple)
    assert restored.anomalies.by_method == {"term_jump": 5, "duplicate_id": 1}


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


def test_anomaly_outcome_without_a_skip_reason() -> None:
    outcome = AnomalyOutcome(total=0, by_method={}, anomalies_key="k")
    assert AnomalyOutcome.from_dict(outcome.to_dict()) == outcome
    assert outcome.to_dict()["ml_skipped"] is None


def test_require_returns_the_value_or_fails_loudly() -> None:
    assert require("x", "section") == "x"
    with pytest.raises(ValueError, match="no 'anomalies'"):
        require(None, "anomalies")
