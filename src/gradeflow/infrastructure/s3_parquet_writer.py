"""S3 adapter for the ProcessedDataWriter port: cleaned data as Parquet."""

import io
from typing import Any

import boto3
import pandas as pd


def processed_key(dataset_id: str) -> str:
    return f"processed/{dataset_id}/data.parquet"


class S3ParquetWriter:
    """Writes cleaned DataFrames to the processed bucket as Parquet."""

    def __init__(self, bucket: str, client: Any = None) -> None:
        self._bucket = bucket
        self._client = client or boto3.client("s3")

    def write(self, dataset_id: str, data: pd.DataFrame) -> str:
        """Serialise in memory (fine for ~50 MB inputs), upload, and return the key."""
        buffer = io.BytesIO()
        data.to_parquet(buffer, index=False)  # pyarrow engine, snappy compression
        key = processed_key(dataset_id)
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=buffer.getvalue(),
            ContentType="application/vnd.apache.parquet",
        )
        return key
