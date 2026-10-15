"""DynamoDB adapter for the DatasetRepository port (single-table design)."""

from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from gradeflow.domain.anomalies import Anomaly
from gradeflow.domain.dataset import DatasetMeta
from gradeflow.domain.statistics import DatasetStats, ScoreSummary, StudentAverage
from gradeflow.infrastructure.table_keys import DATASETS_GSI_PK, dataset_pk, hash_pk

MAX_ANOMALY_RECORDS = 99_999  # the numbering in the sort key has five digits


def anomaly_sk(rank: int) -> str:
    """ANOMALY#00001 for the most important finding. Zero-padded so sort order = rank."""
    return f"ANOMALY#{rank:05d}"


class DynamoDbDatasetRepository:
    """Stores dataset records in the PipelineTable."""

    def __init__(self, table_name: str, resource: Any = None) -> None:
        self._table = (resource or boto3.resource("dynamodb")).Table(table_name)

    def save_meta(self, meta: DatasetMeta) -> None:
        """Write (or overwrite) the DATASET#<id> / META item."""
        self._table.put_item(Item=to_meta_item(meta))

    def create_meta_if_absent(self, meta: DatasetMeta) -> bool:
        """Write the META item only if the dataset has none yet. True if it was created."""
        return self._put_if_absent(to_meta_item(meta))

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
        self._delete_with_prefix(dataset_id, "STATS#")

    def save_anomalies(self, dataset_id: str, anomalies: list[Anomaly]) -> None:
        """Write ANOMALY#00001, ANOMALY#00002, ... in the order given (most important first)."""
        if len(anomalies) > MAX_ANOMALY_RECORDS:
            raise ValueError(f"cannot store more than {MAX_ANOMALY_RECORDS} anomalies")
        with self._table.batch_writer() as batch:
            for rank, anomaly in enumerate(anomalies, start=1):
                batch.put_item(Item=to_anomaly_item(dataset_id, rank, anomaly))

    def delete_anomalies(self, dataset_id: str) -> None:
        """Delete every ANOMALY# item of the dataset (META, STATS and other datasets stay)."""
        self._delete_with_prefix(dataset_id, "ANOMALY#")

    def _delete_with_prefix(self, dataset_id: str, sort_key_prefix: str) -> None:
        query = {
            "KeyConditionExpression": Key("PK").eq(dataset_pk(dataset_id))
            & Key("SK").begins_with(sort_key_prefix),
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
        if self._put_if_absent({**key, "dataset_id": dataset_id}):
            return None
        return self._table.get_item(Key=key, ConsistentRead=True)["Item"]["dataset_id"]

    def _put_if_absent(self, item: dict[str, Any]) -> bool:
        """Conditional put: True if written, False if an item with that key already exists."""
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return False
        return True


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
        "anomaly_count": meta.anomaly_count,
        "anomalies_stored": meta.anomalies_stored,
    }
    if meta.processed_key is not None:
        item["processed_key"] = meta.processed_key
    optional = {
        "file_hash": meta.file_hash,
        "duplicate_of": meta.duplicate_of,
        "quarantine_key": meta.quarantine_key,
        "ml_skipped": meta.ml_skipped,
        "error_message": meta.error_message,
    }
    item.update({name: value for name, value in optional.items() if value is not None})
    return item


def to_anomaly_item(dataset_id: str, rank: int, anomaly: Anomaly) -> dict[str, Any]:
    """Map one finding to a DynamoDB item. Missing (None) fields are left out."""
    item: dict[str, Any] = {
        "PK": dataset_pk(dataset_id),
        "SK": anomaly_sk(rank),
        "rank": rank,
        "method": anomaly.method.value,
        "student_id": anomaly.student_id,
        "severity": _decimal(anomaly.severity),
        "details": anomaly.details,
    }
    optional = {
        "student_name": anomaly.student_name,
        "subject": anomaly.subject,
        "term": anomaly.term,
        "pattern": anomaly.pattern,
        "score": None if anomaly.score is None else _decimal(anomaly.score),
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
