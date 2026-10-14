"""S3 adapter for the FileQuarantine port: keep a copy of rejected files."""

from typing import Any

import boto3


def quarantine_key(dataset_id: str, filename: str) -> str:
    return f"quarantine/{dataset_id}/{filename}"


class S3Quarantine:
    """Copies rejected files into the quarantine bucket.

    It copies instead of moving, so this adapter needs no delete permission on the raw
    bucket. The raw bucket's 30-day lifecycle rule removes the original later.
    """

    def __init__(self, bucket: str, client: Any = None) -> None:
        self._bucket = bucket
        self._client = client or boto3.client("s3")

    def quarantine(self, bucket: str, key: str, dataset_id: str) -> str:
        """Server-side copy bucket/key into quarantine and return the new key."""
        destination = quarantine_key(dataset_id, key.rsplit("/", 1)[-1])
        self._client.copy_object(
            Bucket=self._bucket,
            Key=destination,
            CopySource={"Bucket": bucket, "Key": key},
        )
        return destination
