"""Unit tests for the ValidateDataset use case, using in-memory fakes instead of AWS."""

from datetime import UTC, datetime

import pytest

from gradeflow.application.validate_dataset import ValidateDataset, ValidateDatasetRequest
from gradeflow.domain.dataset import DatasetMeta, DatasetStatus

HEADER = "student_id,student_name,class,subject,term,year,score\n"
GOOD = "S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n"
BAD = "S00002,Nimal Silva,10-B,Maths,T1,2025,150\n"
UPLOADED_AT = datetime(2026, 9, 27, 10, 30, tzinfo=UTC)


class FakeStorage:
    """Stands in for S3: a dict of (bucket, key) -> bytes."""

    def __init__(self, files: dict[tuple[str, str], bytes]) -> None:
        self.files = files

    def read(self, bucket: str, key: str) -> bytes:
        return self.files[(bucket, key)]


class FakeRepository:
    """Stands in for DynamoDB: remembers every saved META record."""

    def __init__(self) -> None:
        self.saved: list[DatasetMeta] = []

    def save_meta(self, meta: DatasetMeta) -> None:
        self.saved.append(meta)


def run(content: str | bytes, key: str = "uploads/ds-1/results.csv") -> tuple[DatasetMeta, list]:
    """Run the use case on one file and return (result, saved records)."""
    data = content.encode() if isinstance(content, str) else content
    repository = FakeRepository()
    use_case = ValidateDataset(FakeStorage({("raw", key): data}), repository, min_valid_ratio=0.8)
    result = use_case.execute(
        ValidateDatasetRequest(dataset_id="ds-1", bucket="raw", key=key, uploaded_at=UPLOADED_AT)
    )
    return result, repository.saved


def test_valid_file_is_completed_and_saved() -> None:
    result, saved = run(HEADER + GOOD * 9 + BAD)

    assert saved == [result]
    assert result == DatasetMeta(
        dataset_id="ds-1",
        filename="results.csv",
        uploaded_at="2026-09-27T10:30:00+00:00",
        status=DatasetStatus.COMPLETED,
        total_rows=10,
        valid_rows=9,
        invalid_rows=1,
        row_errors=("row 10: score '150' must be a number 0-100",),
        error_message=None,
    )


def test_ratio_exactly_at_threshold_passes() -> None:
    result, _ = run(HEADER + GOOD * 8 + BAD * 2)
    assert result.status == DatasetStatus.COMPLETED


def test_too_many_invalid_rows_fails() -> None:
    result, saved = run(HEADER + GOOD * 7 + BAD * 3)

    assert result.status == DatasetStatus.FAILED
    assert result.error_message == "only 70.0% of rows are valid (minimum 80.0%)"
    assert (result.valid_rows, result.invalid_rows) == (7, 3)
    assert saved == [result]


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
    result, saved = run(content, key)

    assert result.status == DatasetStatus.FAILED
    assert message in result.error_message
    assert result.total_rows == 0
    assert saved == [result]


def test_year_limit_comes_from_upload_time() -> None:
    """A 2026 score is valid for a 2026 upload but would be 'in the future' in 2025."""
    result, _ = run(HEADER + GOOD.replace("2025", "2026"))
    assert result.status == DatasetStatus.COMPLETED


def test_filename_is_last_part_of_key() -> None:
    request = ValidateDatasetRequest("ds-1", "raw", "uploads/ds-1/my file.csv", UPLOADED_AT)
    assert request.filename == "my file.csv"
