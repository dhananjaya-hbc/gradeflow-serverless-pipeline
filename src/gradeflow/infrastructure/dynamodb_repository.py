"""DynamoDB adapter for the DatasetRepository port (single-table design)."""

from typing import Any

import boto3

from gradeflow.domain.dataset import DatasetMeta

DATASETS_GSI_PK = "DATASETS"  # all datasets share this GSI1 partition -> list by upload time


def dataset_pk(dataset_id: str) -> str:
    return f"DATASET#{dataset_id}"


class DynamoDbDatasetRepository:
    """Stores dataset records in the PipelineTable."""

    def __init__(self, table_name: str, resource: Any = None) -> None:
        self._table = (resource or boto3.resource("dynamodb")).Table(table_name)

    def save_meta(self, meta: DatasetMeta) -> None:
        """Write (or overwrite) the DATASET#<id> / META item."""
        self._table.put_item(Item=to_meta_item(meta))


def to_meta_item(meta: DatasetMeta) -> dict[str, Any]:
    """Map a DatasetMeta to a DynamoDB item. None values are left out."""
    item: dict[str, Any] = {
        "PK": dataset_pk(meta.dataset_id),
        "SK": "META",
        "GSI1PK": DATASETS_GSI_PK,
        "GSI1SK": meta.uploaded_at,
        "dataset_id": meta.dataset_id,
        "filename": meta.filename,
        "uploaded_at": meta.uploaded_at,
        "status": meta.status.value,
        "total_rows": meta.total_rows,
        "valid_rows": meta.valid_rows,
        "invalid_rows": meta.invalid_rows,
        "row_errors": list(meta.row_errors),
    }
    if meta.error_message is not None:
        item["error_message"] = meta.error_message
    return item
