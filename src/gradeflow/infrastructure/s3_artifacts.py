"""S3 adapter for the DatasetArtifacts port: files the pipeline steps hand to each other.

Both kinds live in the processed bucket:
- work/<dataset_id>/valid.jsonl      validated rows, one JSON object per line (temporary)
- processed/<dataset_id>/data.parquet  the final cleaned data
"""

import io
import json
from typing import Any

import boto3
import pandas as pd

from gradeflow.domain.validation import Row


def valid_rows_key(dataset_id: str) -> str:
    return f"work/{dataset_id}/valid.jsonl"


def clean_data_key(dataset_id: str) -> str:
    return f"processed/{dataset_id}/data.parquet"


class S3DatasetArtifacts:
    """Reads and writes pipeline artifacts in the processed bucket."""

    def __init__(self, bucket: str, client: Any = None) -> None:
        self._bucket = bucket
        self._client = client or boto3.client("s3")

    def save_valid_rows(self, dataset_id: str, rows: list[Row]) -> str:
        """Write rows as JSON Lines (one object per line) and return the key."""
        body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        key = valid_rows_key(dataset_id)
        self._put(key, body.encode("utf-8"), "application/x-ndjson")
        return key

    def load_valid_rows(self, key: str) -> list[Row]:
        text = self._get(key).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def save_clean_data(self, dataset_id: str, data: pd.DataFrame) -> str:
        """Serialise to Parquet in memory (fine for ~50 MB inputs) and return the key."""
        buffer = io.BytesIO()
        data.to_parquet(buffer, index=False)  # pyarrow engine, snappy compression
        key = clean_data_key(dataset_id)
        self._put(key, buffer.getvalue(), "application/vnd.apache.parquet")
        return key

    def load_clean_data(self, key: str) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self._get(key)))

    def _put(self, key: str, body: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType=content_type)

    def _get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
