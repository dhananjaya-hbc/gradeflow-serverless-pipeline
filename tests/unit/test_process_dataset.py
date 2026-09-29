"""Unit tests for the ProcessDataset use case, using in-memory fakes instead of AWS."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from gradeflow.application.process_dataset import ProcessDataset, ProcessDatasetRequest
from gradeflow.domain.dataset import DatasetMeta, DatasetStatus
from gradeflow.domain.statistics import DatasetStats

HEADER = "student_id,student_name,class,subject,term,year,score\n"
GOOD = "S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n"
GOOD_2 = "S00002,Nimal Silva,10-B,maths,T1,2025,40\n"  # lower-case subject gets normalised
BAD = "S00003,Kasun Fernando,10-B,Maths,T1,2025,150\n"
UPLOADED_AT = datetime(2026, 9, 27, 10, 30, tzinfo=UTC)


class FakeStorage:
    """Stands in for S3: a dict of (bucket, key) -> bytes."""

    def __init__(self, files: dict[tuple[str, str], bytes]) -> None:
        self.files = files

    def read(self, bucket: str, key: str) -> bytes:
        return self.files[(bucket, key)]


class FakeWriter:
    """Stands in for the processed bucket: remembers every DataFrame written."""

    def __init__(self) -> None:
        self.written: dict[str, pd.DataFrame] = {}

    def write(self, dataset_id: str, data: pd.DataFrame) -> str:
        key = f"processed/{dataset_id}/data.parquet"
        self.written[key] = data
        return key


class FakeRepository:
    """Stands in for DynamoDB: records every save, in order."""

    def __init__(self) -> None:
        self.metas: list[DatasetMeta] = []
        self.stats: dict[str, DatasetStats] = {}
        self.calls: list[str] = []

    def save_meta(self, meta: DatasetMeta) -> None:
        self.metas.append(meta)
        self.calls.append(f"meta:{meta.status}")

    def save_stats(self, dataset_id: str, stats: DatasetStats) -> None:
        self.stats[dataset_id] = stats
        self.calls.append("stats")


class Harness:
    """Runs the use case on one file and keeps the fakes for assertions."""

    def __init__(self, content: str, key: str = "uploads/ds-1/results.csv") -> None:
        self.writer = FakeWriter()
        self.repository = FakeRepository()
        use_case = ProcessDataset(
            storage=FakeStorage({("raw", key): content.encode()}),
            writer=self.writer,
            repository=self.repository,
            min_valid_ratio=0.8,
            pass_mark=50,
        )
        self.result = use_case.execute(
            ProcessDatasetRequest(dataset_id="ds-1", bucket="raw", key=key, uploaded_at=UPLOADED_AT)
        )


# --- happy path ----------------------------------------------------------------------


def test_valid_file_is_completed() -> None:
    run = Harness(HEADER + GOOD * 4 + GOOD_2 * 4 + BAD + GOOD)

    assert run.result == DatasetMeta(
        dataset_id="ds-1",
        filename="results.csv",
        uploaded_at="2026-09-27T10:30:00+00:00",
        status=DatasetStatus.COMPLETED,
        total_rows=10,
        valid_rows=9,
        invalid_rows=1,
        row_errors=("row 9: score '150' must be a number 0-100",),
        clean_rows=2,  # 9 valid rows, only 2 distinct once cleaned
        duplicates_removed=7,
        processed_key="processed/ds-1/data.parquet",
        error_message=None,
    )


def test_saves_happen_in_order_with_completed_last() -> None:
    run = Harness(HEADER + GOOD + GOOD_2)
    assert run.repository.calls == ["meta:PROCESSING", "stats", "meta:COMPLETED"]
    assert run.repository.metas[-1] == run.result


def test_first_meta_is_processing_with_basic_fields() -> None:
    first = Harness(HEADER + GOOD).repository.metas[0]
    assert first == DatasetMeta(
        dataset_id="ds-1",
        filename="results.csv",
        uploaded_at="2026-09-27T10:30:00+00:00",
        status=DatasetStatus.PROCESSING,
    )


def test_cleaned_data_is_written() -> None:
    run = Harness(HEADER + GOOD + GOOD_2 + GOOD_2)
    data = run.writer.written["processed/ds-1/data.parquet"]

    assert len(data) == 2
    assert list(data["subject"]) == ["Maths", "Maths"]  # 'maths' was normalised


def test_stats_are_computed_with_the_pass_mark() -> None:
    stats = Harness(HEADER + GOOD + GOOD_2).repository.stats["ds-1"]
    assert stats.overall.count == 2
    assert stats.overall.pass_rate == 50.0  # 78.5 passes, 40 fails
    assert stats.student_count == 2


def test_ratio_exactly_at_threshold_passes() -> None:
    run = Harness(HEADER + GOOD * 8 + BAD * 2)
    assert run.result.status == DatasetStatus.COMPLETED


# --- failure paths -------------------------------------------------------------------


def assert_failed_without_outputs(run: Harness) -> None:
    assert run.result.status == DatasetStatus.FAILED
    assert run.repository.calls == ["meta:PROCESSING", "meta:FAILED"]
    assert run.writer.written == {}
    assert run.repository.stats == {}
    assert run.result.processed_key is None


def test_too_many_invalid_rows_fails() -> None:
    run = Harness(HEADER + GOOD * 7 + BAD * 3)

    assert_failed_without_outputs(run)
    assert run.result.error_message == "only 70.0% of rows are valid (minimum 80.0%)"
    assert (run.result.valid_rows, run.result.invalid_rows) == (7, 3)


def test_no_valid_rows_fails_even_with_zero_threshold() -> None:
    writer, repository = FakeWriter(), FakeRepository()
    use_case = ProcessDataset(
        FakeStorage({("raw", "uploads/ds-1/a.csv"): (HEADER + BAD).encode()}),
        writer,
        repository,
        min_valid_ratio=0.0,
        pass_mark=50,
    )
    result = use_case.execute(
        ProcessDatasetRequest("ds-1", "raw", "uploads/ds-1/a.csv", UPLOADED_AT)
    )

    assert result.status == DatasetStatus.FAILED
    assert writer.written == {}
    assert repository.stats == {}


@pytest.mark.parametrize(
    ("content", "key", "message"),
    [
        (HEADER, "uploads/ds-1/results.csv", "no data rows"),
        ("student_id,score\nS00001,50\n", "uploads/ds-1/results.csv", "missing required columns"),
        ("{broken", "uploads/ds-1/results.json", "invalid JSON"),
        ("a,b", "uploads/ds-1/results.txt", "unsupported file type"),
    ],
)
def test_file_level_error_fails_with_message(content: str, key: str, message: str) -> None:
    run = Harness(content, key)

    assert_failed_without_outputs(run)
    assert message in run.result.error_message
    assert run.result.total_rows == 0


# --- request -------------------------------------------------------------------------


def test_year_limit_comes_from_upload_time() -> None:
    """A 2026 score is valid for a 2026 upload but would be 'in the future' in 2025."""
    run = Harness(HEADER + GOOD.replace("2025", "2026"))
    assert run.result.status == DatasetStatus.COMPLETED


def test_filename_is_last_part_of_key() -> None:
    request = ProcessDatasetRequest("ds-1", "raw", "uploads/ds-1/my file.csv", UPLOADED_AT)
    assert request.filename == "my file.csv"
