"""Unit tests for the Anomalies workflow step (rules plus Isolation Forest), on fakes."""

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from scripts.generate_sample_data import inject_problems, make_clean_rows, make_students
from tests.unit.fakes import FakeArtifacts

from gradeflow.application.anomaly_step import DetectAnomaliesStep
from gradeflow.application.pipeline_state import AnomalyOutcome, CleaningOutcome, PipelineState
from gradeflow.domain.cleaning import clean_rows
from gradeflow.domain.validation import split_valid_rows

CLEAN_KEY = "processed/ds-1/data.parquet"


def state() -> PipelineState:
    return PipelineState(
        "ds-1",
        "raw",
        "uploads/ds-1/results.csv",
        "2026-10-14T10:30:00+00:00",
        "a" * 64,
        cleaning=CleaningOutcome(clean_rows=0, duplicates_removed=0, processed_key=CLEAN_KEY),
    )


def run(data: pd.DataFrame, **settings: float) -> tuple[AnomalyOutcome, FakeArtifacts]:
    artifacts = FakeArtifacts()
    artifacts.clean_data[CLEAN_KEY] = data
    options = {"zscore_limit": 3.0, "jump_threshold": 30.0, "contamination": 0.05, **settings}
    return DetectAnomaliesStep(artifacts, **options).execute(state()), artifacts


@pytest.fixture(scope="module")
def sample() -> pd.DataFrame:
    rng = random.Random(42)
    rows, _ = inject_problems(make_clean_rows(make_students(60, rng), 2025, rng), rng)
    return clean_rows(split_valid_rows(rows, current_year=2026)[0]).data


def test_sample_findings_by_method(sample: pd.DataFrame) -> None:
    outcome, _ = run(sample)

    assert outcome.total == 9
    assert outcome.by_method == {"duplicate_id": 1, "term_jump": 5, "isolation_forest": 3}
    assert outcome.ml_skipped is None
    assert outcome.anomalies_key == "work/ds-1/anomalies.jsonl"


def test_findings_are_saved_ranked_most_important_first(sample: pd.DataFrame) -> None:
    outcome, artifacts = run(sample)

    saved = artifacts.anomalies[outcome.anomalies_key]
    assert len(saved) == outcome.total
    assert [a.method.value for a in saved] == ["duplicate_id"] + ["term_jump"] * 5 + [
        "isolation_forest"
    ] * 3
    forest_students = {a.student_id for a in saved if a.method.value == "isolation_forest"}
    assert forest_students == {"S00034", "S00040", "S00055"}  # the planted students


def test_the_outcome_travels_through_json(sample: pd.DataFrame) -> None:
    outcome, _ = run(sample)
    assert AnomalyOutcome.from_dict(json.loads(json.dumps(outcome.to_dict()))) == outcome


def test_thresholds_come_from_the_settings(sample: pd.DataFrame) -> None:
    strict, _ = run(sample, jump_threshold=100.0)
    assert "term_jump" not in strict.by_method

    sensitive, _ = run(sample, jump_threshold=10.0)
    assert sensitive.by_method["term_jump"] > 5

    more_forest, _ = run(sample, contamination=0.2)
    assert more_forest.by_method["isolation_forest"] > 3


def test_a_small_class_runs_the_rules_but_skips_the_forest(sample: pd.DataFrame) -> None:
    ten_students = sample[sample["student_id"].isin(sorted(sample["student_id"].unique())[:10])]

    outcome, _ = run(ten_students)

    assert "isolation_forest" not in outcome.by_method
    assert outcome.ml_skipped == "only 10 students, at least 20 are needed"


def test_clean_data_without_anomalies_still_saves_an_empty_file() -> None:
    rng = random.Random(3)
    data = clean_rows(make_clean_rows(make_students(40, rng), 2025, rng)).data  # nothing planted

    outcome, artifacts = run(data, contamination=0.05)

    assert outcome.by_method.get("term_jump", 0) == 0
    assert outcome.by_method.get("duplicate_id", 0) == 0
    assert artifacts.anomalies[outcome.anomalies_key] is not None
    assert outcome.total == len(artifacts.anomalies[outcome.anomalies_key])


def test_a_state_without_cleaning_fails_loudly() -> None:
    step = DetectAnomaliesStep(FakeArtifacts(), 3.0, 30.0, 0.05)
    bare = PipelineState("ds-1", "raw", "uploads/ds-1/a.csv", "2026-10-14T10:30:00+00:00", "a" * 64)
    with pytest.raises(ValueError, match="cleaning"):
        step.execute(bare)


SRC = Path(__file__).resolve().parents[2] / "src"


def scikit_learn_modules_loaded_by(module: str) -> set[str]:
    """Import `module` in a fresh interpreter and report whether scikit-learn got loaded."""
    code = (
        f"import {module}, sys, json; "
        "print(json.dumps(sorted(m for m in sys.modules if m == 'sklearn')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    return set(json.loads(result.stdout))


def test_zip_lambdas_never_import_scikit_learn() -> None:
    """Only the container Lambda has scikit-learn. These modules are what the zip Lambdas import."""
    for module in (
        "gradeflow.application.pipeline_steps",
        "gradeflow.application.pipeline_state",
        "gradeflow.domain.anomalies",
        "gradeflow.entrypoints.store_handler",
        "gradeflow.entrypoints.trigger_handler",
    ):
        assert scikit_learn_modules_loaded_by(module) == set(), f"{module} imports scikit-learn"


def test_the_anomalies_step_is_the_one_that_needs_it() -> None:
    assert scikit_learn_modules_loaded_by("gradeflow.application.anomaly_step") == {"sklearn"}
