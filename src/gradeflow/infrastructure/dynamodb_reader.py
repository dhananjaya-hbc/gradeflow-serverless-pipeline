"""Read-only DynamoDB adapter for the DatasetReader port (what the Analytics API serves).

Only GetItem and Query are ever called, so the API Lambda's role needs no write access.
"""

import base64
import binascii
import json
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

from gradeflow.application.queries import BadRequest
from gradeflow.application.read_ports import Page, StatsRecords
from gradeflow.infrastructure.dynamodb_repository import DATASETS_GSI_PK, dataset_pk

INTERNAL_KEYS = frozenset({"PK", "SK", "GSI1PK", "GSI1SK"})  # table plumbing, not data
INDEX_KEY_NAMES = ("PK", "SK", "GSI1PK", "GSI1SK")


class DynamoDbDatasetReader:
    """Reads dataset records, statistics and anomalies from the PipelineTable."""

    def __init__(self, table_name: str, resource: Any = None) -> None:
        self._table = (resource or boto3.resource("dynamodb")).Table(table_name)

    def list_datasets(self, limit: int, cursor: str | None) -> Page:
        """META records, newest first, through the GSI1 index (HASH# items are not in it)."""
        query: dict[str, Any] = {
            "IndexName": "GSI1",
            "KeyConditionExpression": Key("GSI1PK").eq(DATASETS_GSI_PK),
            "ScanIndexForward": False,  # newest upload first
            "Limit": limit + 1,  # one extra tells us whether another page exists
        }
        if cursor:
            query["ExclusiveStartKey"] = _decode_dataset_cursor(cursor)
        items = self._table.query(**query)["Items"]
        page, more = items[:limit], len(items) > limit
        next_cursor = _encode({name: page[-1][name] for name in INDEX_KEY_NAMES}) if more else None
        return Page([_plain(item) for item in page], next_cursor)

    def get_meta(self, dataset_id: str) -> dict[str, Any] | None:
        item = self._table.get_item(Key={"PK": dataset_pk(dataset_id), "SK": "META"}).get("Item")
        return _plain(item) if item else None

    def get_stats(self, dataset_id: str) -> StatsRecords:
        items = self._query_all(
            Key("PK").eq(dataset_pk(dataset_id)) & Key("SK").begins_with("STATS#")
        )
        overall = next((item for item in items if item["SK"] == "STATS#OVERALL"), None)
        groups = [item for item in items if item["SK"].startswith("STATS#SUBJECT#")]
        return StatsRecords(
            overall=_plain(overall) if overall else None,
            groups=[_plain(item) for item in sorted(groups, key=lambda g: g["SK"])],
        )

    def list_anomalies(
        self, dataset_id: str, limit: int, cursor: str | None, method: str | None
    ) -> Page:
        """Findings in rank order. With a method filter the page is still filled to `limit`
        (DynamoDB filters after reading, so we keep reading until we have enough)."""
        query: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(dataset_pk(dataset_id))
            & Key("SK").begins_with("ANOMALY#"),
        }
        if method:
            query["FilterExpression"] = Attr("method").eq(method)
        if cursor:
            query["ExclusiveStartKey"] = {
                "PK": dataset_pk(dataset_id),
                "SK": _decode_anomaly_cursor(cursor),
            }

        found: list[dict[str, Any]] = []
        while True:
            if (
                not method
            ):  # without a filter DynamoDB's Limit is exact, so read no more than needed
                query["Limit"] = limit + 1 - len(found)
            page = self._table.query(**query)
            found += page["Items"]
            if len(found) > limit or "LastEvaluatedKey" not in page:
                break
            query["ExclusiveStartKey"] = page["LastEvaluatedKey"]

        kept, more = found[:limit], len(found) > limit
        next_cursor = _encode({"SK": kept[-1]["SK"]}) if more else None
        return Page([_plain(item) for item in kept], next_cursor)

    def _query_all(self, key_condition: Any) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"KeyConditionExpression": key_condition}
        items: list[dict[str, Any]] = []
        while True:
            page = self._table.query(**query)
            items += page["Items"]
            if "LastEvaluatedKey" not in page:
                return items
            query["ExclusiveStartKey"] = page["LastEvaluatedKey"]


# --- plain types ---------------------------------------------------------------------


def _plain(value: Any) -> Any:
    """DynamoDB hands back Decimal; JSON and the use cases want int and float. Whole numbers
    become int (DynamoDB does not remember 100.0 versus 100, and JSON does not care)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if k not in INTERNAL_KEYS}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


# --- cursors -------------------------------------------------------------------------


def _encode(key: dict[str, str]) -> str:
    """An opaque, URL-safe cursor. It is only a position: it carries no permissions."""
    return base64.urlsafe_b64encode(json.dumps(key, sort_keys=True).encode()).decode()


def _decode(cursor: str) -> dict[str, Any]:
    try:
        key = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise BadRequest("the cursor is not valid", "invalid_cursor") from exc
    if not isinstance(key, dict) or not all(isinstance(v, str) for v in key.values()):
        raise BadRequest("the cursor is not valid", "invalid_cursor")
    return key


def _decode_dataset_cursor(cursor: str) -> dict[str, str]:
    """The position in the dataset listing. Checked strictly: a forged cursor must not be
    able to point DynamoDB at some other part of the table."""
    key = _decode(cursor)
    valid = (
        set(key) == set(INDEX_KEY_NAMES)
        and key["GSI1PK"] == DATASETS_GSI_PK
        and key["PK"].startswith("DATASET#")
        and key["SK"] == "META"
    )
    if not valid:
        raise BadRequest("the cursor is not valid", "invalid_cursor")
    return key


def _decode_anomaly_cursor(cursor: str) -> str:
    """The sort key to continue after. The partition always comes from the URL, never the cursor."""
    key = _decode(cursor)
    sort_key = key.get("SK", "")
    if set(key) != {"SK"} or not sort_key.startswith("ANOMALY#"):
        raise BadRequest("the cursor is not valid", "invalid_cursor")
    return sort_key
