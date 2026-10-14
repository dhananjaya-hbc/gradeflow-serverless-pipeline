"""Unit tests for the trigger Lambda handler (SQS event parsing + wiring), no AWS."""

import json
from datetime import UTC, datetime

import pytest
from tests.unit.fakes import FakeRepository, FakeStorage, FakeWorkflow

from gradeflow.application.pipeline_steps import TriggerPipeline
from gradeflow.entrypoints import trigger_handler
from gradeflow.entrypoints.trigger_handler import (
    dataset_id_from_key,
    parse_record,
    process_event,
    s3_records,
)
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
from gradeflow.infrastructure.s3_storage import S3FileStorage
from gradeflow.infrastructure.step_functions_starter import StepFunctionsStarter

CSV = (
    b"student_id,student_name,class,subject,term,year,score\n"
    b"S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n"
)


def s3_record(key: str, bucket: str = "raw", event_time: str = "2026-10-14T10:30:00.123Z") -> dict:
    """Minimal S3 ObjectCreated record, shaped like the real event."""
    return {
        "eventTime": event_time,
        "s3": {"bucket": {"name": bucket}, "object": {"key": key, "size": len(CSV)}},
    }


def sqs_message(*s3_event_records: dict, message_id: str = "m-1") -> dict:
    """An SQS record whose body is the S3 event, as the S3 -> SQS notification delivers it."""
    return {"messageId": message_id, "body": json.dumps({"Records": list(s3_event_records)})}


def sqs_event(*messages: dict) -> dict:
    return {"Records": list(messages)}


class Fakes:
    """A TriggerPipeline wired to in-memory fakes, plus handles to inspect them."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.repository = FakeRepository()
        self.workflow = FakeWorkflow()
        self.use_case = TriggerPipeline(
            FakeStorage({("raw", key): content for key, content in files.items()}),
            self.repository,
            self.workflow,
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
    assert request.uploaded_at == datetime(2026, 10, 14, 10, 30, 0, 123000, tzinfo=UTC)


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


# --- triggering ----------------------------------------------------------------------


def test_process_event_starts_a_run_and_skips_stray_keys() -> None:
    fakes = Fakes({"uploads/ds-1/results.csv": CSV})
    event = sqs_event(sqs_message(s3_record("uploads/ds-1/results.csv"), s3_record("stray.csv")))

    result = process_event(event, fakes.use_case)

    assert result == {
        "triggered": [{"dataset_id": "ds-1", "status": "STARTED"}],
        "skipped": ["stray.csv"],
    }
    [(name, payload)] = fakes.workflow.started
    assert name.startswith("ds-1-")
    assert payload["key"] == "uploads/ds-1/results.csv"


def test_process_event_reports_started_and_duplicate_uploads() -> None:
    fakes = Fakes({"uploads/ds-1/a.csv": CSV, "uploads/ds-2/b.csv": CSV})
    event = sqs_event(
        sqs_message(s3_record("uploads/ds-1/a.csv"), message_id="m-1"),
        sqs_message(s3_record("uploads/ds-2/b.csv"), message_id="m-2"),
    )

    result = process_event(event, fakes.use_case)

    assert result["triggered"] == [
        {"dataset_id": "ds-1", "status": "STARTED"},
        {"dataset_id": "ds-2", "status": "DUPLICATE"},
    ]
    assert len(fakes.workflow.started) == 1


def test_redelivered_message_does_not_start_a_second_run() -> None:
    fakes = Fakes({"uploads/ds-1/results.csv": CSV})
    event = sqs_event(sqs_message(s3_record("uploads/ds-1/results.csv")))
    process_event(event, fakes.use_case)

    result = process_event(event, fakes.use_case)

    assert result["triggered"] == [{"dataset_id": "ds-1", "status": "ALREADY_STARTED"}]
    assert len(fakes.workflow.started) == 1


def test_process_event_lets_errors_propagate() -> None:
    """A missing file is an infrastructure error: raise, so SQS retries and then uses the DLQ."""
    fakes = Fakes({})  # the file the event points to does not exist
    event = sqs_event(sqs_message(s3_record("uploads/ds-1/results.csv")))

    with pytest.raises(KeyError):
        process_event(event, fakes.use_case)


def test_process_event_without_records() -> None:
    assert process_event({}, use_case=None) == {"triggered": [], "skipped": []}


# --- wiring --------------------------------------------------------------------------


def test_handler_builds_use_case_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """handler() wires real adapters from config (no AWS call happens while building)."""
    arn = "arn:aws:states:ap-south-1:123456789012:stateMachine:pipeline"
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("TABLE_NAME", "PipelineTable")
    monkeypatch.setenv("STATE_MACHINE_ARN", arn)
    trigger_handler._build_use_case.cache_clear()
    try:
        use_case = trigger_handler._build_use_case()
        assert isinstance(use_case._storage, S3FileStorage)
        assert isinstance(use_case._repository, DynamoDbDatasetRepository)
        assert isinstance(use_case._workflow, StepFunctionsStarter)
        assert use_case._workflow._arn == arn
        assert use_case is trigger_handler._build_use_case()  # cached per container
    finally:
        trigger_handler._build_use_case.cache_clear()
