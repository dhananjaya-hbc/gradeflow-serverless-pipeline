"""S3 adapter for the UploadSigner port: pre-signed POST forms for the raw bucket."""

from typing import Any

import boto3
from botocore.config import Config

from gradeflow.application.read_ports import SignedUpload

# SigV4 and virtual-hosted addressing make the form's URL the REGIONAL endpoint
# (bucket.s3.<region>.amazonaws.com). A default client returns the global endpoint, which
# redirects for buckets outside us-east-1, and a browser cannot follow a redirect on a
# cross-origin form POST.
SIGNING_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})


class S3UploadSigner:
    """Signs forms that allow ONE upload to the raw bucket."""

    def __init__(self, bucket: str, client: Any = None) -> None:
        self._bucket = bucket
        self._client = client or boto3.client("s3", config=SIGNING_CONFIG)

    def create_upload(self, key: str, max_bytes: int, expires_in: int) -> SignedUpload:
        """A form for exactly `key`, from 1 byte up to `max_bytes`, valid `expires_in` seconds.

        S3 enforces the conditions itself when the form is posted: another key, an empty
        file, a file over the limit or an expired form are all refused by S3.
        """
        form = self._client.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Conditions=[["content-length-range", 1, max_bytes]],
            ExpiresIn=expires_in,
        )
        return SignedUpload(url=form["url"], fields=form["fields"])
