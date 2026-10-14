"""Lambda handler: SQS message (wrapping an S3 upload event) -> process the dataset.

Handler path in template.yaml: gradeflow.entrypoints.process_handler.handler

Errors are deliberately not caught here: if processing raises, the Lambda call fails, SQS
shows the message again, and after the queue's maxReceiveCount it lands in the DLQ.
"""

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from functools import cache
from typing import Any
from urllib.parse import unquote_plus

from gradeflow.application.process_dataset import ProcessDataset, ProcessDatasetRequest
from gradeflow.infrastructure.config import load_config
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
from gradeflow.infrastructure.s3_parquet_writer import S3ParquetWriter
from gradeflow.infrastructure.s3_quarantine import S3Quarantine
from gradeflow.infrastructure.s3_storage import S3FileStorage

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return process_event(event, _build_use_case())


@cache
def _build_use_case() -> ProcessDataset:
    """Build adapters once per Lambda container; warm invocations reuse them."""
    config = load_config()
    return ProcessDataset(
        storage=S3FileStorage(),
        writer=S3ParquetWriter(config.processed_bucket),
        quarantine=S3Quarantine(config.quarantine_bucket),
        repository=DynamoDbDatasetRepository(config.table_name),
        min_valid_ratio=config.min_valid_ratio,
        pass_mark=config.pass_mark,
    )


def process_event(event: dict[str, Any], use_case: ProcessDataset) -> dict[str, Any]:
    """Process every uploaded file in an SQS event. Returns a short summary."""
    processed, skipped = [], []
    for record in s3_records(event):
        request = parse_record(record)
        if request is None:
            skipped.append(record["s3"]["object"]["key"])
            continue
        meta = use_case.execute(request)
        logger.info(
            "processed dataset %s: status=%s valid=%d/%d clean=%d duplicate_of=%s",
            meta.dataset_id,
            meta.status,
            meta.valid_rows,
            meta.total_rows,
            meta.clean_rows,
            meta.duplicate_of,
        )
        processed.append({"dataset_id": meta.dataset_id, "status": meta.status.value})
    return {"processed": processed, "skipped": skipped}


def s3_records(event: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield the S3 records inside each SQS message body.

    S3 also sends a one-off "s3:TestEvent" message when the queue notification is created;
    it has no "Records" and is skipped. A body that is not valid JSON raises, so the message
    is retried and finally parked in the DLQ.
    """
    for message in event.get("Records", []):
        yield from json.loads(message["body"]).get("Records", [])


def parse_record(record: dict[str, Any]) -> ProcessDatasetRequest | None:
    """Turn one S3 event record into a request, or None if the key has the wrong shape."""
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])  # S3 URL-encodes keys ("a+b.csv")
    dataset_id = dataset_id_from_key(key)
    if dataset_id is None:
        logger.warning("skipping %s: expected key 'uploads/<dataset_id>/<filename>'", key)
        return None
    return ProcessDatasetRequest(
        dataset_id=dataset_id,
        bucket=bucket,
        key=key,
        uploaded_at=datetime.fromisoformat(record["eventTime"]),
    )


def dataset_id_from_key(key: str) -> str | None:
    """'uploads/ds-1/results.csv' -> 'ds-1'. Returns None for any other shape."""
    parts = key.split("/")
    if len(parts) == 3 and parts[0] == "uploads" and parts[1] and parts[2]:
        return parts[1]
    return None
