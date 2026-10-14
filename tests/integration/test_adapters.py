"""Integration tests for the boto3 adapters, against moto's in-memory fake AWS."""

import json
from collections.abc import Iterator
from decimal import Decimal

import boto3
import pandas as pd
import pytest
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from moto import mock_aws

from gradeflow.domain.dataset import DatasetMeta, DatasetStatus
from gradeflow.domain.statistics import (
    DatasetStats,
    ScoreSummary,
    StudentAverage,
    SubjectTermStats,
)
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
from gradeflow.infrastructure.s3_artifacts import S3DatasetArtifacts
from gradeflow.infrastructure.s3_quarantine import S3Quarantine
from gradeflow.infrastructure.s3_storage import S3FileStorage
from gradeflow.infrastructure.step_functions_starter import StepFunctionsStarter

REGION = "ap-south-1"
TABLE = "PipelineTable"


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fake credentials so nothing can ever reach a real AWS account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield


@pytest.fixture
def table():
    """Create a PipelineTable with the same keys and GSI as template.yaml."""
    return boto3.resource("dynamodb").create_table(
        TableName=TABLE,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": name, "AttributeType": "S"}
            for name in ("PK", "SK", "GSI1PK", "GSI1SK")
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def make_meta(dataset_id: str = "ds-1", **overrides: object) -> DatasetMeta:
    fields = {
        "dataset_id": dataset_id,
        "filename": "results.csv",
        "uploaded_at": "2026-09-27T10:30:00+00:00",
        "status": DatasetStatus.COMPLETED,
        "total_rows": 10,
        "valid_rows": 9,
        "invalid_rows": 1,
        "row_errors": ("row 10: score '150' must be a number 0-100",),
        **overrides,
    }
    return DatasetMeta(**fields)


# --- S3 ------------------------------------------------------------------------------


def test_s3_storage_reads_object() -> None:
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="raw", CreateBucketConfiguration={"LocationConstraint": REGION})
    s3.put_object(Bucket="raw", Key="uploads/ds-1/results.csv", Body=b"a,b\n1,2\n")

    assert S3FileStorage().read("raw", "uploads/ds-1/results.csv") == b"a,b\n1,2\n"


def test_s3_storage_missing_object_raises() -> None:
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="raw", CreateBucketConfiguration={"LocationConstraint": REGION})

    with pytest.raises(s3.exceptions.NoSuchKey):
        S3FileStorage().read("raw", "uploads/missing.csv")


# --- DynamoDB ------------------------------------------------------------------------


def test_save_meta_writes_item(table) -> None:
    DynamoDbDatasetRepository(TABLE).save_meta(make_meta())

    item = table.get_item(Key={"PK": "DATASET#ds-1", "SK": "META"})["Item"]
    assert item == {
        "PK": "DATASET#ds-1",
        "SK": "META",
        "GSI1PK": "DATASETS",
        "GSI1SK": "2026-09-27T10:30:00+00:00",
        "dataset_id": "ds-1",
        "filename": "results.csv",
        "uploaded_at": "2026-09-27T10:30:00+00:00",
        "status": "COMPLETED",
        "total_rows": 10,
        "valid_rows": 9,
        "invalid_rows": 1,
        "row_errors": ["row 10: score '150' must be a number 0-100"],
        "clean_rows": 0,
        "duplicates_removed": 0,
    }


def test_completed_meta_stores_cleaning_results(table) -> None:
    meta = make_meta(
        clean_rows=8, duplicates_removed=1, processed_key="processed/ds-1/data.parquet"
    )
    DynamoDbDatasetRepository(TABLE).save_meta(meta)

    item = table.get_item(Key={"PK": "DATASET#ds-1", "SK": "META"})["Item"]
    assert (item["clean_rows"], item["duplicates_removed"]) == (8, 1)
    assert item["processed_key"] == "processed/ds-1/data.parquet"


def test_meta_stores_hash_duplicate_and_quarantine_fields(table) -> None:
    meta = make_meta(
        status=DatasetStatus.DUPLICATE,
        file_hash="a" * 64,
        duplicate_of="ds-0",
        quarantine_key="quarantine/ds-1/results.csv",
    )
    DynamoDbDatasetRepository(TABLE).save_meta(meta)

    item = table.get_item(Key={"PK": "DATASET#ds-1", "SK": "META"})["Item"]
    assert item["status"] == "DUPLICATE"
    assert item["file_hash"] == "a" * 64
    assert item["duplicate_of"] == "ds-0"
    assert item["quarantine_key"] == "quarantine/ds-1/results.csv"


def test_failed_meta_stores_error_message(table) -> None:
    meta = make_meta(status=DatasetStatus.FAILED, error_message="file contains no data rows")
    DynamoDbDatasetRepository(TABLE).save_meta(meta)

    item = table.get_item(Key={"PK": "DATASET#ds-1", "SK": "META"})["Item"]
    assert item["status"] == "FAILED"
    assert item["error_message"] == "file contains no data rows"


def test_save_meta_overwrites_existing_item(table) -> None:
    repository = DynamoDbDatasetRepository(TABLE)
    repository.save_meta(make_meta(status=DatasetStatus.PROCESSING))
    repository.save_meta(make_meta(status=DatasetStatus.COMPLETED))

    assert table.scan()["Count"] == 1
    assert table.get_item(Key={"PK": "DATASET#ds-1", "SK": "META"})["Item"]["status"] == "COMPLETED"


def test_gsi_lists_datasets_newest_first(table) -> None:
    repository = DynamoDbDatasetRepository(TABLE)
    repository.save_meta(make_meta("old", uploaded_at="2026-09-01T00:00:00+00:00"))
    repository.save_meta(make_meta("new", uploaded_at="2026-09-27T00:00:00+00:00"))

    response = table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq("DATASETS"),
        ScanIndexForward=False,
    )
    assert [item["dataset_id"] for item in response["Items"]] == ["new", "old"]


def make_stats() -> DatasetStats:
    maths = ScoreSummary(
        count=4, mean=57.5, median=55.0, std=17.08, min=40.0, max=80.0, pass_rate=75.0
    )
    overall = ScoreSummary(
        count=8, mean=58.25, median=55.0, std=20.6, min=30.0, max=90.0, pass_rate=62.5
    )
    return DatasetStats(
        overall=overall,
        student_count=4,
        by_subject_term=(
            SubjectTermStats("Maths", "T1", maths),
            SubjectTermStats("Social Studies", "T2", maths),
        ),
        top_students=(StudentAverage("S00001", "Amal Perera", 85.0),),
        bottom_students=(StudentAverage("S00002", "Nimal Silva", 35.0),),
        class_averages={"10-A": 60.0, "10-B": 56.5},
    )


def test_save_stats_writes_overall_item(table) -> None:
    DynamoDbDatasetRepository(TABLE).save_stats("ds-1", make_stats())

    item = table.get_item(Key={"PK": "DATASET#ds-1", "SK": "STATS#OVERALL"})["Item"]
    assert item == {
        "PK": "DATASET#ds-1",
        "SK": "STATS#OVERALL",
        "count": 8,
        "mean": Decimal("58.25"),
        "median": Decimal("55.0"),
        "std": Decimal("20.6"),
        "min": Decimal("30.0"),
        "max": Decimal("90.0"),
        "pass_rate": Decimal("62.5"),
        "student_count": 4,
        "top_students": [
            {"student_id": "S00001", "student_name": "Amal Perera", "average": Decimal("85.0")}
        ],
        "bottom_students": [
            {"student_id": "S00002", "student_name": "Nimal Silva", "average": Decimal("35.0")}
        ],
        "class_averages": {"10-A": Decimal("60.0"), "10-B": Decimal("56.5")},
    }


def test_save_stats_writes_one_item_per_subject_term(table) -> None:
    DynamoDbDatasetRepository(TABLE).save_stats("ds-1", make_stats())

    items = table.query(
        KeyConditionExpression=Key("PK").eq("DATASET#ds-1")
        & Key("SK").begins_with("STATS#SUBJECT#")
    )["Items"]
    assert [item["SK"] for item in items] == [
        "STATS#SUBJECT#Maths#T1",
        "STATS#SUBJECT#Social Studies#T2",
    ]
    assert items[0]["subject"] == "Maths"
    assert items[0]["term"] == "T1"
    assert items[0]["mean"] == Decimal("57.5")
    assert items[0]["pass_rate"] == Decimal("75.0")


def test_save_stats_handles_more_than_one_batch(table) -> None:
    """batch_writer splits into requests of 25 items; 40 groups need two requests."""
    base = make_stats()
    groups = tuple(SubjectTermStats(f"Subject {i:02d}", "T1", base.overall) for i in range(40))
    stats = DatasetStats(
        overall=base.overall,
        student_count=base.student_count,
        by_subject_term=groups,
        top_students=base.top_students,
        bottom_students=base.bottom_students,
        class_averages=base.class_averages,
    )
    DynamoDbDatasetRepository(TABLE).save_stats("ds-1", stats)

    assert table.query(KeyConditionExpression=Key("PK").eq("DATASET#ds-1"))["Count"] == 41


# --- S3 artifacts (work files and final Parquet) -------------------------------------


@pytest.fixture
def processed_bucket():
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="processed", CreateBucketConfiguration={"LocationConstraint": REGION})
    return s3


def clean_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "student_id": ["S00001", "S00002"],
            "student_name": ["Amal Perera", "Nimal Silva"],
            "class": ["10-A", "10-B"],
            "subject": ["Maths", "ICT"],
            "term": ["T1", "T2"],
            "year": pd.Series([2025, 2025], dtype="int64"),
            "score": [78.5, 40.0],
        }
    )


def test_clean_data_round_trip(processed_bucket) -> None:
    artifacts = S3DatasetArtifacts("processed")
    data = clean_frame()

    key = artifacts.save_clean_data("ds-1", data)

    assert key == "processed/ds-1/data.parquet"
    stored = processed_bucket.get_object(Bucket="processed", Key=key)
    assert stored["ContentType"] == "application/vnd.apache.parquet"
    pd.testing.assert_frame_equal(artifacts.load_clean_data(key), data)  # values and dtypes


def test_valid_rows_round_trip_as_json_lines(processed_bucket) -> None:
    artifacts = S3DatasetArtifacts("processed")
    rows = [
        {"student_id": "S00001", "student_name": "Amal Perera", "year": "2025", "score": "78.5"},
        {
            "student_id": "S00002",
            "student_name": "Ruwan \u0dc1\u0dca\u0dbb\u0dd3",
            "year": 2025,
            "score": 40,
        },
        {"student_id": "S00003", "student_name": None, "extra": 1.5, "score": 0},
    ]

    key = artifacts.save_valid_rows("ds-1", rows)

    assert key == "work/ds-1/valid.jsonl"
    body = processed_bucket.get_object(Bucket="processed", Key=key)["Body"].read().decode()
    assert len(body.splitlines()) == 3  # one JSON object per line
    assert all(json.loads(line) for line in body.splitlines())
    assert artifacts.load_valid_rows(key) == rows  # strings, numbers, None and unicode survive


def test_valid_rows_with_newlines_quotes_and_commas_stay_on_one_line(processed_bucket) -> None:
    artifacts = S3DatasetArtifacts("processed")
    rows = [{"student_name": 'A "quoted"\nname, with comma', "score": "50"}]

    key = artifacts.save_valid_rows("ds-1", rows)

    body = processed_bucket.get_object(Bucket="processed", Key=key)["Body"].read().decode()
    assert len(body.splitlines()) == 1  # the newline inside the value is escaped
    assert artifacts.load_valid_rows(key) == rows


def test_empty_valid_rows_round_trip(processed_bucket) -> None:
    artifacts = S3DatasetArtifacts("processed")
    key = artifacts.save_valid_rows("ds-1", [])
    assert artifacts.load_valid_rows(key) == []


def test_datasets_do_not_share_artifact_keys(processed_bucket) -> None:
    artifacts = S3DatasetArtifacts("processed")
    key_1 = artifacts.save_valid_rows("ds-1", [{"score": "1"}])
    key_2 = artifacts.save_valid_rows("ds-2", [{"score": "2"}])

    assert artifacts.load_valid_rows(key_1) == [{"score": "1"}]
    assert artifacts.load_valid_rows(key_2) == [{"score": "2"}]


def test_loading_a_missing_artifact_raises(processed_bucket) -> None:
    with pytest.raises(processed_bucket.exceptions.NoSuchKey):
        S3DatasetArtifacts("processed").load_valid_rows("work/nope/valid.jsonl")


# --- Step Functions starter ----------------------------------------------------------


@pytest.fixture
def state_machine() -> str:
    """A trivial state machine, enough for start_execution to work against."""
    definition = json.dumps({"StartAt": "Done", "States": {"Done": {"Type": "Pass", "End": True}}})
    return boto3.client("stepfunctions").create_state_machine(
        name="pipeline", definition=definition, roleArn="arn:aws:iam::123456789012:role/test"
    )["stateMachineArn"]


def test_start_runs_the_state_machine_with_the_payload(state_machine) -> None:
    payload = {"dataset_id": "ds-1", "key": "uploads/ds-1/a.csv"}

    assert StepFunctionsStarter(state_machine).start("ds-1-run", payload) is True

    sfn = boto3.client("stepfunctions")
    [execution] = sfn.list_executions(stateMachineArn=state_machine)["executions"]
    assert execution["name"] == "ds-1-run"
    described = sfn.describe_execution(executionArn=execution["executionArn"])
    assert json.loads(described["input"]) == payload


def test_start_with_an_existing_name_returns_false_and_starts_nothing(state_machine) -> None:
    starter = StepFunctionsStarter(state_machine)
    assert starter.start("ds-1-run", {"n": 1}) is True

    assert starter.start("ds-1-run", {"n": 1}) is False

    executions = boto3.client("stepfunctions").list_executions(stateMachineArn=state_machine)
    assert len(executions["executions"]) == 1


def test_start_with_different_names_starts_both(state_machine) -> None:
    starter = StepFunctionsStarter(state_machine)
    assert starter.start("run-a", {}) is True
    assert starter.start("run-b", {}) is True


def test_start_raises_other_errors_so_sqs_can_retry() -> None:
    missing = "arn:aws:states:ap-south-1:123456789012:stateMachine:does-not-exist"
    with pytest.raises(ClientError):
        StepFunctionsStarter(missing).start("run-1", {})


# --- create_meta_if_absent -----------------------------------------------------------


def test_create_meta_if_absent_creates_only_once(table) -> None:
    repository = DynamoDbDatasetRepository(TABLE)

    assert repository.create_meta_if_absent(make_meta(status=DatasetStatus.PROCESSING)) is True
    assert repository.create_meta_if_absent(make_meta(status=DatasetStatus.FAILED)) is False

    item = table.get_item(Key={"PK": "DATASET#ds-1", "SK": "META"})["Item"]
    assert item["status"] == "PROCESSING"


def test_create_meta_if_absent_never_overwrites_a_finished_dataset(table) -> None:
    repository = DynamoDbDatasetRepository(TABLE)
    repository.save_meta(make_meta(status=DatasetStatus.COMPLETED))

    assert repository.create_meta_if_absent(make_meta(status=DatasetStatus.PROCESSING)) is False

    item = table.get_item(Key={"PK": "DATASET#ds-1", "SK": "META"})["Item"]
    assert item["status"] == "COMPLETED"


# --- idempotency: file hash claims ---------------------------------------------------


def test_first_claim_wins_and_second_gets_the_owner(table) -> None:
    repository = DynamoDbDatasetRepository(TABLE)

    assert repository.claim_file_hash("f" * 64, "ds-1") is None
    assert repository.claim_file_hash("f" * 64, "ds-2") == "ds-1"

    item = table.get_item(Key={"PK": "HASH#" + "f" * 64, "SK": "HASH"})["Item"]
    assert item == {"PK": "HASH#" + "f" * 64, "SK": "HASH", "dataset_id": "ds-1"}


def test_claiming_again_with_the_same_id_returns_that_id(table) -> None:
    """A retry of the same upload sees its own claim, which the use case treats as a retry."""
    repository = DynamoDbDatasetRepository(TABLE)
    repository.claim_file_hash("e" * 64, "ds-1")

    assert repository.claim_file_hash("e" * 64, "ds-1") == "ds-1"


def test_different_hashes_do_not_collide(table) -> None:
    repository = DynamoDbDatasetRepository(TABLE)

    assert repository.claim_file_hash("1" * 64, "ds-1") is None
    assert repository.claim_file_hash("2" * 64, "ds-2") is None


def test_hash_items_stay_out_of_the_dataset_listing(table) -> None:
    repository = DynamoDbDatasetRepository(TABLE)
    repository.save_meta(make_meta("ds-1"))
    repository.claim_file_hash("d" * 64, "ds-1")

    listing = table.query(IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq("DATASETS"))
    assert [item["dataset_id"] for item in listing["Items"]] == ["ds-1"]


# --- re-processing: deleting stats ---------------------------------------------------


def test_delete_stats_removes_only_stats_items(table) -> None:
    repository = DynamoDbDatasetRepository(TABLE)
    repository.save_meta(make_meta("ds-1"))
    repository.save_stats("ds-1", make_stats())
    repository.save_meta(make_meta("ds-2"))
    repository.save_stats("ds-2", make_stats())

    repository.delete_stats("ds-1")

    ds1 = table.query(KeyConditionExpression=Key("PK").eq("DATASET#ds-1"))["Items"]
    ds2 = table.query(KeyConditionExpression=Key("PK").eq("DATASET#ds-2"))["Items"]
    assert [item["SK"] for item in ds1] == ["META"]
    assert len(ds2) == 4  # META + STATS#OVERALL + 2 subject/term items


def test_delete_stats_handles_more_than_one_batch(table) -> None:
    base = make_stats()
    groups = tuple(SubjectTermStats(f"Subject {i:02d}", "T1", base.overall) for i in range(60))
    stats = DatasetStats(
        overall=base.overall,
        student_count=base.student_count,
        by_subject_term=groups,
        top_students=base.top_students,
        bottom_students=base.bottom_students,
        class_averages=base.class_averages,
    )
    repository = DynamoDbDatasetRepository(TABLE)
    repository.save_stats("ds-1", stats)

    repository.delete_stats("ds-1")

    assert table.query(KeyConditionExpression=Key("PK").eq("DATASET#ds-1"))["Count"] == 0


def test_delete_stats_for_unknown_dataset_is_a_no_op(table) -> None:
    DynamoDbDatasetRepository(TABLE).delete_stats("nope")


# --- S3 quarantine -------------------------------------------------------------------


def test_quarantine_copies_the_file_and_keeps_the_original() -> None:
    s3 = boto3.client("s3")
    for bucket in ("raw", "quarantine"):
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION})
    s3.put_object(Bucket="raw", Key="uploads/ds-1/broken.csv", Body=b"student_id,score\n")

    key = S3Quarantine("quarantine").quarantine("raw", "uploads/ds-1/broken.csv", "ds-1")

    assert key == "quarantine/ds-1/broken.csv"
    copied = s3.get_object(Bucket="quarantine", Key=key)["Body"].read()
    assert copied == b"student_id,score\n"
    assert s3.get_object(Bucket="raw", Key="uploads/ds-1/broken.csv")["Body"].read() == copied


def test_quarantine_missing_source_raises() -> None:
    s3 = boto3.client("s3")
    for bucket in ("raw", "quarantine"):
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION})

    with pytest.raises(s3.exceptions.NoSuchKey):
        S3Quarantine("quarantine").quarantine("raw", "uploads/ds-1/missing.csv", "ds-1")
