"""DynamoDB adapter for the DatasetRepository port (single-table design)."""

from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from gradeflow.domain.dataset import DatasetMeta
from gradeflow.domain.statistics import DatasetStats, ScoreSummary, StudentAverage

DATASETS_GSI_PK = "DATASETS"  # all datasets share this GSI1 partition -> list by upload time


def dataset_pk(dataset_id: str) -> str:
    return f"DATASET#{dataset_id}"


def hash_pk(sha256: str) -> str:
    return f"HASH#{sha256}"


class DynamoDbDatasetRepository:
    """Stores dataset records in the PipelineTable."""

    def __init__(self, table_name: str, resource: Any = None) -> None:
        self._table = (resource or boto3.resource("dynamodb")).Table(table_name)

    def save_meta(self, meta: DatasetMeta) -> None:
        """Write (or overwrite) the DATASET#<id> / META item."""
        self._table.put_item(Item=to_meta_item(meta))

    def save_stats(self, dataset_id: str, stats: DatasetStats) -> None:
        """Write the STATS#OVERALL item and one STATS#SUBJECT#<subject>#<term> item per group.

        batch_writer groups the puts into BatchWriteItem calls (25 items each) and retries
        any unprocessed items for us.
        """
        with self._table.batch_writer() as batch:
            for item in to_stats_items(dataset_id, stats):
                batch.put_item(Item=item)

    def delete_stats(self, dataset_id: str) -> None:
        """Delete every STATS# item of the dataset (META and other datasets are untouched)."""
        query = {
            "KeyConditionExpression": Key("PK").eq(dataset_pk(dataset_id))
            & Key("SK").begins_with("STATS#"),
            "ProjectionExpression": "PK, SK",
        }
        with self._table.batch_writer() as batch:
            while True:
                page = self._table.query(**query)  # a page holds at most 1 MB of items
                for item in page["Items"]:
                    batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
                if "LastEvaluatedKey" not in page:
                    return
                query["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def claim_file_hash(self, sha256: str, dataset_id: str) -> str | None:
        """Atomically claim a file hash. Returns None if won, else the owning dataset_id.

        The condition makes the write succeed for exactly one caller, even if two uploads of
        the same file are processed at the same moment.
        """
        key = {"PK": hash_pk(sha256), "SK": "HASH"}
        try:
            self._table.put_item(
                Item={**key, "dataset_id": dataset_id},
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return self._table.get_item(Key=key, ConsistentRead=True)["Item"]["dataset_id"]
        return None


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
        "clean_rows": meta.clean_rows,
        "duplicates_removed": meta.duplicates_removed,
    }
    if meta.processed_key is not None:
        item["processed_key"] = meta.processed_key
    optional = {
        "file_hash": meta.file_hash,
        "duplicate_of": meta.duplicate_of,
        "quarantine_key": meta.quarantine_key,
        "error_message": meta.error_message,
    }
    item.update({name: value for name, value in optional.items() if value is not None})
    return item


def to_stats_items(dataset_id: str, stats: DatasetStats) -> list[dict[str, Any]]:
    """Map DatasetStats to DynamoDB items: one overall item plus one per subject/term."""
    pk = dataset_pk(dataset_id)
    overall = {
        "PK": pk,
        "SK": "STATS#OVERALL",
        **_summary_attributes(stats.overall),
        "student_count": stats.student_count,
        "top_students": [_student_map(s) for s in stats.top_students],
        "bottom_students": [_student_map(s) for s in stats.bottom_students],
        "class_averages": {name: _decimal(avg) for name, avg in stats.class_averages.items()},
    }
    groups = [
        {
            "PK": pk,
            "SK": f"STATS#SUBJECT#{group.subject}#{group.term}",
            "subject": group.subject,
            "term": group.term,
            **_summary_attributes(group.summary),
        }
        for group in stats.by_subject_term
    ]
    return [overall, *groups]


def _summary_attributes(summary: ScoreSummary) -> dict[str, Any]:
    return {
        "count": summary.count,
        "mean": _decimal(summary.mean),
        "median": _decimal(summary.median),
        "std": _decimal(summary.std),
        "min": _decimal(summary.min),
        "max": _decimal(summary.max),
        "pass_rate": _decimal(summary.pass_rate),
    }


def _student_map(student: StudentAverage) -> dict[str, Any]:
    return {
        "student_id": student.student_id,
        "student_name": student.student_name,
        "average": _decimal(student.average),
    }


def _decimal(value: float) -> Decimal:
    """boto3 rejects Python floats; str() keeps the rounded value exact (57.5 -> '57.5')."""
    return Decimal(str(value))
