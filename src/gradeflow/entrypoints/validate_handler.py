"""Lambda handler: S3 upload event -> validate the file -> save META to DynamoDB.

Handler path in template.yaml: gradeflow.entrypoints.validate_handler.handler
"""

import logging
from datetime import datetime
from functools import cache
from typing import Any
from urllib.parse import unquote_plus

from gradeflow.application.validate_dataset import ValidateDataset, ValidateDatasetRequest
from gradeflow.infrastructure.config import load_config
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
from gradeflow.infrastructure.s3_storage import S3FileStorage

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return process_event(event, _build_use_case())


@cache
def _build_use_case() -> ValidateDataset:
    """Build adapters once per Lambda container; warm invocations reuse them."""
    config = load_config()
    return ValidateDataset(
        storage=S3FileStorage(),
        repository=DynamoDbDatasetRepository(config.table_name),
        min_valid_ratio=config.min_valid_ratio,
    )


def process_event(event: dict[str, Any], use_case: ValidateDataset) -> dict[str, Any]:
    """Validate every uploaded file in an S3 event. Returns a short summary."""
    processed, skipped = [], []
    for record in event.get("Records", []):
        request = parse_record(record)
        if request is None:
            skipped.append(record["s3"]["object"]["key"])
            continue
        meta = use_case.execute(request)
        logger.info(
            "validated dataset %s: status=%s valid=%d/%d",
            meta.dataset_id,
            meta.status,
            meta.valid_rows,
            meta.total_rows,
        )
        processed.append({"dataset_id": meta.dataset_id, "status": meta.status.value})
    return {"processed": processed, "skipped": skipped}


def parse_record(record: dict[str, Any]) -> ValidateDatasetRequest | None:
    """Turn one S3 event record into a request, or None if the key has the wrong shape."""
    bucket = record["s3"]["bucket"]["name"]
    key = unquote_plus(record["s3"]["object"]["key"])  # S3 URL-encodes keys ("a+b.csv")
    dataset_id = dataset_id_from_key(key)
    if dataset_id is None:
        logger.warning("skipping %s: expected key 'uploads/<dataset_id>/<filename>'", key)
        return None
    return ValidateDatasetRequest(
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
