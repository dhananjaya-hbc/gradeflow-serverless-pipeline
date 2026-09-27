"""S3 adapter for the FileStorage port."""

from typing import Any

import boto3


class S3FileStorage:
    """Reads uploaded files from S3."""

    def __init__(self, client: Any = None) -> None:
        self._client = client or boto3.client("s3")

    def read(self, bucket: str, key: str) -> bytes:
        """Download the whole object into memory (fine for files up to ~50 MB)."""
        response = self._client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
