"""Fixtures shared by the integration tests (moto: a pretend AWS in memory)."""

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

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
