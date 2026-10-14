"""Unit tests for the process Lambda handler (SQS event parsing + wiring), no AWS."""

import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from gradeflow.application.process_dataset import ProcessDataset
from gradeflow.domain.dataset import DatasetMeta
from gradeflow.domain.statistics import DatasetStats
from gradeflow.entrypoints import process_handler
from gradeflow.entrypoints.process_handler import (
    dataset_id_from_key,
    parse_record,
    process_event,
    s3_records,
)
from gradeflow.infrastructure.s3_parquet_writer import S3ParquetWriter
from gradeflow.infrastructure.s3_quarantine import S3Quarantine

CSV = (
    b"student_id,student_name,class,subject,term,year,score\n"
    b"S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n"
)
BROKEN_CSV = b"student_id,score\nS00001,50\n"


def s3_record(key: str, bucket: str = "raw") -> dict:
    """Minimal S3 ObjectCreated record, shaped like the real event."""
    return {
        "eventTime": "2026-09-28T10:30:00.123Z",
        "s3": {"bucket": {"name": bucket}, "object": {"key": key, "size": len(CSV)}},
    }


def sqs_message(*s3_event_records: dict, message_id: str = "m-1") -> dict:
    """An SQS record whose body is the S3 event, as the S3 -> SQS notification delivers it."""
    return {"messageId": message_id, "body": json.dumps({"Records": list(s3_event_records)})}


def sqs_event(*messages: dict) -> dict:
    return {"Records": list(messages)}


class FakeStorage:
    def __init__(self, files: dict[tuple[str, str], bytes]) -> None:
        self.files = files

    def read(self, bucket: str, key: str) -> bytes:
        return self.files[(bucket, key)]


class FakeWriter:
    def __init__(self) -> None:
        self.written: dict[str, pd.DataFrame] = {}

    def write(self, dataset_id: str, data: pd.DataFrame) -> str:
        key = f"processed/{dataset_id}/data.parquet"
        self.written[key] = data
        return key


class FakeQuarantine:
    def __init__(self) -> None:
        self.copied: list[str] = []

    def quarantine(self, bucket: str, key: str, dataset_id: str) -> str:
        self.copied.append(key)
        return f"quarantine/{dataset_id}/{key.rsplit('/', 1)[-1]}"


class FakeRepository:
    def __init__(self) -> None:
        self.metas: list[DatasetMeta] = []
        self.stats: dict[str, DatasetStats] = {}
        self.claims: dict[str, str] = {}

    def save_meta(self, meta: DatasetMeta) -> None:
        self.metas.append(meta)

    def save_stats(self, dataset_id: str, stats: DatasetStats) -> None:
        self.stats[dataset_id] = stats

    def delete_stats(self, dataset_id: str) -> None:
        self.stats.pop(dataset_id, None)

    def claim_file_hash(self, sha256: str, dataset_id: str) -> str | None:
        owner = self.claims.get(sha256)
        if owner is None:
            self.claims[sha256] = dataset_id
        return owner


class Fakes:
    """A use case wired to in-memory fakes, plus handles to inspect them."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.writer = FakeWriter()
        self.quarantine = FakeQuarantine()
        self.repository = FakeRepository()
        self.use_case = ProcessDataset(
            storage=FakeStorage({("raw", key): content for key, content in files.items()}),
            writer=self.writer,
            quarantine=self.quarantine,
            repository=self.repository,
            min_valid_ratio=0.8,
            pass_mark=50,
        )


# --- key and record parsing ----------------------------------------------------------


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


# --- unwrapping SQS messages ---------------------------------------------------------


def test_s3_records_unwraps_every_message() -> None:
    first, second, third = (s3_record(f"uploads/ds-{i}/a.csv") for i in (1, 2, 3))
    event = sqs_event(sqs_message(first, second, message_id="m-1"), sqs_message(third))

    assert list(s3_records(event)) == [first, second, third]


def test_s3_test_event_is_ignored() -> None:
    """S3 sends this once when the queue notification is created; it has no Records."""
    test_event = {
        "messageId": "m-0",
        "body": json.dumps({"Event": "s3:TestEvent", "Bucket": "raw"}),
    }
    assert list(s3_records(sqs_event(test_event))) == []


def test_invalid_json_body_raises_so_sqs_retries() -> None:
    with pytest.raises(json.JSONDecodeError):
        list(s3_records(sqs_event({"messageId": "m-1", "body": "not json"})))


def test_event_without_records_yields_nothing() -> None:
    assert list(s3_records({})) == []


# --- processing ----------------------------------------------------------------------


def test_process_event_processes_and_skips() -> None:
    fakes = Fakes({"uploads/ds-1/results.csv": CSV})
    event = sqs_event(sqs_message(s3_record("uploads/ds-1/results.csv"), s3_record("stray.csv")))

    result = process_event(event, fakes.use_case)

    assert result == {
        "processed": [{"dataset_id": "ds-1", "status": "COMPLETED"}],
        "skipped": ["stray.csv"],
    }
    assert [meta.status for meta in fakes.repository.metas] == ["PROCESSING", "COMPLETED"]
    assert list(fakes.writer.written) == ["processed/ds-1/data.parquet"]
    assert fakes.repository.stats["ds-1"].overall.count == 1


def test_process_event_reports_duplicates_and_quarantined_files() -> None:
    fakes = Fakes(
        {
            "uploads/ds-1/results.csv": CSV,
            "uploads/ds-2/copy.csv": CSV,
            "uploads/ds-3/broken.csv": BROKEN_CSV,
        }
    )
    event = sqs_event(
        sqs_message(s3_record("uploads/ds-1/results.csv"), message_id="m-1"),
        sqs_message(s3_record("uploads/ds-2/copy.csv"), message_id="m-2"),
        sqs_message(s3_record("uploads/ds-3/broken.csv"), message_id="m-3"),
    )

    result = process_event(event, fakes.use_case)

    assert result["processed"] == [
        {"dataset_id": "ds-1", "status": "COMPLETED"},
        {"dataset_id": "ds-2", "status": "DUPLICATE"},
        {"dataset_id": "ds-3", "status": "FAILED"},
    ]
    assert fakes.quarantine.copied == ["uploads/ds-3/broken.csv"]
    assert list(fakes.writer.written) == ["processed/ds-1/data.parquet"]


def test_process_event_lets_use_case_errors_propagate() -> None:
    """A missing file is an infrastructure error: raise, so SQS retries and then uses the DLQ."""
    fakes = Fakes({})  # the file the event points to does not exist
    event = sqs_event(sqs_message(s3_record("uploads/ds-1/results.csv")))

    with pytest.raises(KeyError):
        process_event(event, fakes.use_case)


def test_process_event_without_records() -> None:
    assert process_event({}, use_case=None) == {"processed": [], "skipped": []}


# --- wiring --------------------------------------------------------------------------


def test_handler_builds_use_case_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """handler() wires real adapters from config (no AWS call happens while building)."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("TABLE_NAME", "PipelineTable")
    monkeypatch.setenv("PROCESSED_BUCKET", "processed-bucket")
    monkeypatch.setenv("QUARANTINE_BUCKET", "quarantine-bucket")
    monkeypatch.setenv("MIN_VALID_RATIO", "0.9")
    monkeypatch.setenv("PASS_MARK", "40")
    process_handler._build_use_case.cache_clear()
    try:
        use_case = process_handler._build_use_case()
        assert use_case._min_valid_ratio == 0.9
        assert use_case._pass_mark == 40.0
        assert isinstance(use_case._writer, S3ParquetWriter)
        assert use_case._writer._bucket == "processed-bucket"
        assert isinstance(use_case._quarantine, S3Quarantine)
        assert use_case._quarantine._bucket == "quarantine-bucket"
        assert use_case is process_handler._build_use_case()  # cached per container
    finally:
        process_handler._build_use_case.cache_clear()
