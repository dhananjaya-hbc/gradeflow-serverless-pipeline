"""Integration tests for the boto3 adapters, against moto's in-memory fake AWS."""

from collections.abc import Iterator

import boto3
import pytest
from boto3.dynamodb.conditions import Key
from moto import mock_aws

from gradeflow.domain.dataset import DatasetMeta, DatasetStatus
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
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
    }


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
