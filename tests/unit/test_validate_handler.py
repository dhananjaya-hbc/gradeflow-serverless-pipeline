"""Unit tests for the validate Lambda handler (event parsing + wiring), no AWS."""

from datetime import UTC, datetime

import pytest

from gradeflow.application.validate_dataset import ValidateDataset
from gradeflow.domain.dataset import DatasetMeta
from gradeflow.entrypoints import validate_handler
from gradeflow.entrypoints.validate_handler import dataset_id_from_key, parse_record, process_event

CSV = (
    b"student_id,student_name,class,subject,term,year,score\n"
    b"S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n"
)


def s3_record(key: str, bucket: str = "raw") -> dict:
    """Minimal S3 ObjectCreated record, shaped like the real event."""
    return {
        "eventTime": "2026-09-28T10:30:00.123Z",
        "s3": {"bucket": {"name": bucket}, "object": {"key": key, "size": len(CSV)}},
    }


class FakeStorage:
    def __init__(self, files: dict[tuple[str, str], bytes]) -> None:
        self.files = files

    def read(self, bucket: str, key: str) -> bytes:
        return self.files[(bucket, key)]


class FakeRepository:
    def __init__(self) -> None:
        self.saved: list[DatasetMeta] = []

    def save_meta(self, meta: DatasetMeta) -> None:
        self.saved.append(meta)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("uploads/ds-1/results.csv", "ds-1"),
        ("uploads/ds-1/my file.csv", "ds-1"),
        ("results.csv", None),
        ("uploads/results.csv", None),
        ("uploads//results.csv", None),
        ("uploads/ds-1/", None),
        ("other/ds-1/results.csv", None),
        ("uploads/ds-1/nested/results.csv", None),
    ],
)
def test_dataset_id_from_key(key: str, expected: str | None) -> None:
    assert dataset_id_from_key(key) == expected


def test_parse_record_decodes_key_and_time() -> None:
    request = parse_record(s3_record("uploads/ds-1/my+file%281%29.csv"))

    assert request is not None
    assert request.dataset_id == "ds-1"
    assert request.bucket == "raw"
    assert request.key == "uploads/ds-1/my file(1).csv"
    assert request.filename == "my file(1).csv"
    assert request.uploaded_at == datetime(2026, 9, 28, 10, 30, 0, 123000, tzinfo=UTC)


def test_parse_record_rejects_bad_key() -> None:
    assert parse_record(s3_record("random.csv")) is None


def test_process_event_validates_and_skips() -> None:
    repository = FakeRepository()
    use_case = ValidateDataset(
        FakeStorage({("raw", "uploads/ds-1/results.csv"): CSV}), repository, min_valid_ratio=0.8
    )
    event = {"Records": [s3_record("uploads/ds-1/results.csv"), s3_record("stray.csv")]}

    result = process_event(event, use_case)

    assert result == {
        "processed": [{"dataset_id": "ds-1", "status": "COMPLETED"}],
        "skipped": ["stray.csv"],
    }
    assert [meta.dataset_id for meta in repository.saved] == ["ds-1"]


def test_process_event_without_records() -> None:
    assert process_event({}, use_case=None) == {"processed": [], "skipped": []}


def test_handler_builds_use_case_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """handler() wires real adapters from config; here we only check it reads TABLE_NAME."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("TABLE_NAME", "PipelineTable")
    monkeypatch.setenv("MIN_VALID_RATIO", "0.9")
    validate_handler._build_use_case.cache_clear()
    try:
        use_case = validate_handler._build_use_case()
        assert use_case._min_valid_ratio == 0.9
        assert use_case is validate_handler._build_use_case()  # cached per container
    finally:
        validate_handler._build_use_case.cache_clear()
