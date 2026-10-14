"""End-to-end flow of the steps, emulating the state machine (no AWS).

Every hand-over goes through real JSON text, like Step Functions does, so a field that
would not survive serialisation fails here. The sample numbers are the Phase 2 and 3 results
that were verified on AWS: the split into steps must not change them.
"""

import csv
import io
import json
import random
from datetime import UTC, datetime
from typing import Any

from scripts.generate_sample_data import COLUMNS, inject_problems, make_clean_rows, make_students
from tests.unit.fakes import (
    FakeArtifacts,
    FakeQuarantine,
    FakeRepository,
    FakeStorage,
    FakeWorkflow,
)

from gradeflow.application.pipeline_state import PipelineState
from gradeflow.application.pipeline_steps import (
    CleanStep,
    RejectDataset,
    StatsStep,
    StoreStep,
    TriggerPipeline,
    TriggerRequest,
    TriggerStatus,
    ValidateStep,
)
from gradeflow.domain.dataset import DatasetStatus

UPLOADED_AT = datetime(2026, 10, 14, 10, 30, tzinfo=UTC)


def sample_csv() -> str:
    """The seed-42 sample: 904 rows, 10 invalid, 3 exact duplicates."""
    rng = random.Random(42)
    rows, _ = inject_problems(make_clean_rows(make_students(60, rng), 2025, rng), rng)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def over_json(data: dict[str, Any]) -> dict[str, Any]:
    """What a Step Functions hand-over does to a payload."""
    return json.loads(json.dumps(data))


class Pipeline:
    """Runs one upload through the steps in the order the state machine does."""

    def __init__(self, files: dict[str, str]) -> None:
        self.storage = FakeStorage({("raw", key): text.encode() for key, text in files.items()})
        self.artifacts = FakeArtifacts()
        self.quarantine = FakeQuarantine()
        self.repository = FakeRepository()
        self.workflow = FakeWorkflow()
        self.trigger = TriggerPipeline(self.storage, self.repository, self.workflow)

    def run(self, dataset_id: str, filename: str = "results.csv") -> TriggerStatus:
        key = f"uploads/{dataset_id}/{filename}"
        status = self.trigger.execute(TriggerRequest(dataset_id, "raw", key, UPLOADED_AT))
        if status == TriggerStatus.STARTED:
            self._execute(over_json(self.workflow.started[-1][1]))
        return status

    def _execute(self, data: dict[str, Any]) -> None:
        state = PipelineState.from_dict(data)
        validation = ValidateStep(self.storage, self.artifacts, 0.8).execute(state)
        data["validation"] = over_json(validation.to_dict())
        if not validation.valid:  # the Choice state
            RejectDataset(self.quarantine, self.repository).execute(PipelineState.from_dict(data))
            return
        data["cleaning"] = over_json(
            CleanStep(self.artifacts).execute(PipelineState.from_dict(data)).to_dict()
        )
        data["stats"] = over_json(
            StatsStep(self.artifacts, 50).execute(PipelineState.from_dict(data))
        )
        StoreStep(self.repository).execute(PipelineState.from_dict(data))


def test_sample_file_gives_the_verified_phase_3_results() -> None:
    pipeline = Pipeline({"uploads/ds-1/results.csv": sample_csv()})

    assert pipeline.run("ds-1") == TriggerStatus.STARTED

    meta = pipeline.repository.current["ds-1"]
    assert meta.status == DatasetStatus.COMPLETED
    assert (meta.total_rows, meta.valid_rows, meta.invalid_rows) == (904, 894, 10)
    assert (meta.clean_rows, meta.duplicates_removed) == (891, 3)
    assert meta.processed_key == "processed/ds-1/data.parquet"
    assert len(meta.row_errors) == 10
    assert meta.quarantine_key is None
    assert len(pipeline.artifacts.clean_data[meta.processed_key]) == 891


def test_sample_file_gives_the_verified_statistics() -> None:
    pipeline = Pipeline({"uploads/ds-1/results.csv": sample_csv()})
    pipeline.run("ds-1")

    stats = pipeline.repository.stats["ds-1"]
    assert (stats.overall.count, stats.overall.mean, stats.overall.median) == (891, 62.43, 62.0)
    assert (stats.overall.std, stats.overall.pass_rate) == (16.12, 78.34)
    assert stats.student_count == 60
    assert stats.class_averages == {"10-A": 62.11, "10-B": 62.75}
    assert len(stats.by_subject_term) == 15  # 5 subjects x 3 terms
    assert [s.student_id for s in stats.top_students][:2] == ["S00034", "S00047"]


def test_meta_goes_processing_then_completed_and_stats_are_stored_before_completion() -> None:
    pipeline = Pipeline({"uploads/ds-1/results.csv": sample_csv()})
    pipeline.run("ds-1")

    assert pipeline.repository.calls == [
        "claim",
        "create_meta:PROCESSING",
        "delete_stats",
        "stats",
        "meta:COMPLETED",
    ]


def test_broken_file_takes_the_failure_branch() -> None:
    pipeline = Pipeline({"uploads/ds-1/broken.csv": "student_id,score\nS00001,50\n"})

    assert pipeline.run("ds-1", "broken.csv") == TriggerStatus.STARTED

    meta = pipeline.repository.current["ds-1"]
    assert meta.status == DatasetStatus.FAILED
    assert "missing required columns" in meta.error_message
    assert meta.quarantine_key == "quarantine/ds-1/broken.csv"
    assert pipeline.artifacts.clean_data == {}  # Clean and Stats never ran
    assert pipeline.repository.stats == {}


def test_duplicate_upload_never_reaches_the_workflow() -> None:
    csv_text = sample_csv()
    pipeline = Pipeline(
        {"uploads/ds-1/results.csv": csv_text, "uploads/ds-2/results.csv": csv_text}
    )
    pipeline.run("ds-1")

    assert pipeline.run("ds-2") == TriggerStatus.DUPLICATE

    assert len(pipeline.workflow.started) == 1
    assert pipeline.repository.current["ds-2"].status == DatasetStatus.DUPLICATE
    assert pipeline.repository.current["ds-2"].duplicate_of == "ds-1"
    assert pipeline.repository.current["ds-1"].status == DatasetStatus.COMPLETED
    assert "processed/ds-2/data.parquet" not in pipeline.artifacts.clean_data


def test_two_datasets_do_not_interfere() -> None:
    other = sample_csv().replace("S00001", "S00099", 1)  # different bytes, so not a duplicate
    pipeline = Pipeline(
        {"uploads/ds-1/results.csv": sample_csv(), "uploads/ds-2/results.csv": other}
    )

    pipeline.run("ds-1")
    pipeline.run("ds-2")

    assert pipeline.repository.current["ds-1"].status == DatasetStatus.COMPLETED
    assert pipeline.repository.current["ds-2"].status == DatasetStatus.COMPLETED
    assert set(pipeline.repository.stats) == {"ds-1", "ds-2"}
