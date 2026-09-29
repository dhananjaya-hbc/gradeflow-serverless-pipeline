"""Integration tests for the boto3 adapters, against moto's in-memory fake AWS."""

import io
from collections.abc import Iterator
from decimal import Decimal

import boto3
import pandas as pd
import pytest
from boto3.dynamodb.conditions import Key
from moto import mock_aws

from gradeflow.domain.dataset import DatasetMeta, DatasetStatus
from gradeflow.domain.statistics import (
    DatasetStats,
    ScoreSummary,
    StudentAverage,
    SubjectTermStats,
)
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
from gradeflow.infrastructure.s3_parquet_writer import S3ParquetWriter
from gradeflow.infrastructure.s3_storage import S3FileStorage

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


# --- S3 Parquet ----------------------------------------------------------------------


def test_parquet_writer_round_trip() -> None:
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="processed", CreateBucketConfiguration={"LocationConstraint": REGION})
    data = pd.DataFrame(
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

    key = S3ParquetWriter("processed").write("ds-1", data)

    assert key == "processed/ds-1/data.parquet"
    obj = s3.get_object(Bucket="processed", Key=key)
    assert obj["ContentType"] == "application/vnd.apache.parquet"
    read_back = pd.read_parquet(io.BytesIO(obj["Body"].read()))
    pd.testing.assert_frame_equal(read_back, data)  # same values, columns and dtypes
