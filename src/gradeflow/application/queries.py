"""Read-side use cases for the Analytics API.

Plain dicts in and out, so a handler only has to turn the result into JSON. Imports nothing
that needs pandas: the API Lambda runs without the pandas layer (a test enforces it).

Errors are `QueryError`s carrying the HTTP status and a stable error code, which the handler
renders as {"error": {"code": ..., "message": ...}}.
"""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from secrets import token_hex
from typing import Any

from gradeflow.application.read_ports import DatasetReader, UploadSigner

DATASET_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
FILENAME_PATTERN = re.compile(r"[A-Za-z0-9._ -]{1,100}")
UPLOAD_EXTENSIONS = (".csv", ".json")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # the size the pipeline is documented and tested for
UPLOAD_EXPIRY_SECONDS = 900

DATASETS_PAGE = (20, 100)  # (default, maximum) page sizes
ANOMALIES_PAGE = (100, 500)  # a dataset stores at most 500 anomalies by default
# Must equal the values of domain.anomalies.AnomalyMethod (a test checks it): that module
# needs pandas, which this one must not import.
ANOMALY_METHODS = frozenset({"zscore", "term_jump", "duplicate_id", "isolation_forest"})

SUMMARY_FIELDS = ("count", "mean", "median", "std", "min", "max", "pass_rate")
ANOMALY_FIELDS = (
    "rank",
    "method",
    "student_id",
    "student_name",
    "subject",
    "term",
    "score",
    "pattern",
    "severity",
    "details",
)


class QueryError(Exception):
    """A request the API must answer with an error status."""

    status = 400
    code = "bad_request"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class BadRequest(QueryError):
    status = 400


class NotFound(QueryError):
    status = 404
    code = "not_found"


# --- input checks --------------------------------------------------------------------


def check_dataset_id(dataset_id: str) -> str:
    if not DATASET_ID_PATTERN.fullmatch(dataset_id or ""):
        raise BadRequest(
            "dataset id may contain only letters, digits, '.', '_' and '-' (1 to 64 characters)",
            "invalid_dataset_id",
        )
    return dataset_id


def page_size(raw: str | int | None, default_and_maximum: tuple[int, int]) -> int:
    default, maximum = default_and_maximum
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if not 1 <= value <= maximum:
        raise BadRequest(f"limit must be a whole number from 1 to {maximum}", "invalid_limit")
    return value


# --- use cases -----------------------------------------------------------------------


class ListDatasets:
    """Datasets, newest first, as short summaries."""

    def __init__(self, reader: DatasetReader) -> None:
        self._reader = reader

    def execute(self, limit: str | int | None = None, cursor: str | None = None) -> dict[str, Any]:
        page = self._reader.list_datasets(page_size(limit, DATASETS_PAGE), cursor or None)
        return {
            "datasets": [_summary(meta) for meta in page.items],
            "next_cursor": page.next_cursor,
        }


class GetDataset:
    """One dataset with its validation report."""

    def __init__(self, reader: DatasetReader) -> None:
        self._reader = reader

    def execute(self, dataset_id: str) -> dict[str, Any]:
        meta = _require(self._reader, check_dataset_id(dataset_id))
        return {
            **_summary(meta),
            "validation": {
                "total_rows": meta.get("total_rows", 0),
                "valid_rows": meta.get("valid_rows", 0),
                "invalid_rows": meta.get("invalid_rows", 0),
                "row_errors": meta.get("row_errors", []),
                "error_message": meta.get("error_message"),
            },
            "cleaning": {
                "clean_rows": meta.get("clean_rows", 0),
                "duplicates_removed": meta.get("duplicates_removed", 0),
                "processed_key": meta.get("processed_key"),
            },
            "anomalies": {
                "count": meta.get("anomaly_count", 0),
                "stored": meta.get("anomalies_stored", 0),
                "ml_skipped": meta.get("ml_skipped"),
            },
            "quarantine_key": meta.get("quarantine_key"),
            "file_hash": meta.get("file_hash"),
        }


class GetStats:
    """Overall, per-subject and per-term statistics. Empty (not an error) until completed."""

    def __init__(self, reader: DatasetReader) -> None:
        self._reader = reader

    def execute(self, dataset_id: str) -> dict[str, Any]:
        meta = _require(self._reader, check_dataset_id(dataset_id))
        records = self._reader.get_stats(dataset_id)
        overall = records.overall
        return {
            "dataset_id": dataset_id,
            "status": meta.get("status"),
            "overall": {field: overall.get(field) for field in SUMMARY_FIELDS} if overall else None,
            "student_count": overall.get("student_count") if overall else None,
            "top_students": overall.get("top_students", []) if overall else [],
            "bottom_students": overall.get("bottom_students", []) if overall else [],
            "class_averages": overall.get("class_averages", {}) if overall else {},
            "by_subject_term": [
                {"subject": group["subject"], "term": group["term"]}
                | {field: group.get(field) for field in SUMMARY_FIELDS}
                for group in records.groups
            ],
        }


class GetAnomalies:
    """Findings in rank order, optionally only one method, paged."""

    def __init__(self, reader: DatasetReader) -> None:
        self._reader = reader

    def execute(
        self,
        dataset_id: str,
        method: str | None = None,
        limit: str | int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        meta = _require(self._reader, check_dataset_id(dataset_id))
        if method and method not in ANOMALY_METHODS:
            raise BadRequest(
                f"method must be one of: {', '.join(sorted(ANOMALY_METHODS))}", "invalid_method"
            )
        page = self._reader.list_anomalies(
            dataset_id, page_size(limit, ANOMALIES_PAGE), cursor or None, method or None
        )
        return {
            "dataset_id": dataset_id,
            "status": meta.get("status"),
            "anomaly_count": meta.get("anomaly_count", 0),
            "anomalies_stored": meta.get("anomalies_stored", 0),
            "ml_skipped": meta.get("ml_skipped"),
            "items": [{field: item.get(field) for field in ANOMALY_FIELDS} for item in page.items],
            "next_cursor": page.next_cursor,
        }


class CreateUpload:
    """Sign an upload form for a NEW dataset. The server picks the dataset id, so nobody can
    upload under an existing id and overwrite another dataset's results."""

    def __init__(
        self,
        signer: UploadSigner,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        suffix: Callable[[], str] = lambda: token_hex(4),
    ) -> None:
        self._signer = signer
        self._clock = clock
        self._suffix = suffix

    def execute(self, filename: Any) -> dict[str, Any]:
        name = _check_filename(filename)
        dataset_id = f"ds-{self._clock():%Y%m%d}-{self._suffix()}"
        key = f"uploads/{dataset_id}/{name}"
        signed = self._signer.create_upload(key, MAX_UPLOAD_BYTES, UPLOAD_EXPIRY_SECONDS)
        return {
            "dataset_id": dataset_id,
            "key": key,
            "upload": {"method": "POST", "url": signed.url, "fields": signed.fields},
            "max_bytes": MAX_UPLOAD_BYTES,
            "expires_in": UPLOAD_EXPIRY_SECONDS,
        }


# --- helpers -------------------------------------------------------------------------


def _check_filename(filename: Any) -> str:
    if not isinstance(filename, str) or not filename.strip():
        raise BadRequest("filename is required", "invalid_filename")
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()  # drop any folder part
    if not FILENAME_PATTERN.fullmatch(name) or name.startswith("."):
        raise BadRequest(
            "filename may contain only letters, digits, spaces, '.', '_' and '-'",
            "invalid_filename",
        )
    if not name.lower().endswith(UPLOAD_EXTENSIONS):
        raise BadRequest("only .csv and .json files can be uploaded", "unsupported_file_type")
    return name


def _require(reader: DatasetReader, dataset_id: str) -> dict[str, Any]:
    meta = reader.get_meta(dataset_id)
    if meta is None:
        raise NotFound(f"dataset '{dataset_id}' does not exist")
    return meta


def _summary(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_id": meta.get("dataset_id"),
        "filename": meta.get("filename"),
        "uploaded_at": meta.get("uploaded_at"),
        "status": meta.get("status"),
        "total_rows": meta.get("total_rows", 0),
        "valid_rows": meta.get("valid_rows", 0),
        "clean_rows": meta.get("clean_rows", 0),
        "anomaly_count": meta.get("anomaly_count", 0),
        "duplicate_of": meta.get("duplicate_of"),
        "error_message": meta.get("error_message"),
    }
