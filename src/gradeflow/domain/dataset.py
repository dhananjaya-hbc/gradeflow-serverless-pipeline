"""Dataset entity: the metadata we track for every uploaded file."""

from dataclasses import dataclass
from enum import StrEnum


class DatasetStatus(StrEnum):
    """Lifecycle of an uploaded dataset."""

    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"  # same file content as an earlier dataset; skipped, not processed


@dataclass(frozen=True)
class DatasetMeta:
    """Metadata and validation summary for one uploaded dataset."""

    dataset_id: str
    filename: str
    uploaded_at: str  # ISO 8601, UTC
    status: DatasetStatus
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    row_errors: tuple[str, ...] = ()
    clean_rows: int = 0  # rows left after cleaning (valid rows minus duplicates)
    duplicates_removed: int = 0
    processed_key: str | None = None  # where the cleaned Parquet file was written
    anomaly_count: int = 0  # every anomaly found (only the most important are stored)
    anomalies_stored: int = 0  # how many ANOMALY# items were written
    ml_skipped: str | None = None  # why the Isolation Forest did not run, if it did not
    file_hash: str | None = None  # SHA-256 of the uploaded file (idempotency key)
    duplicate_of: str | None = None  # dataset_id that first uploaded identical content
    quarantine_key: str | None = None  # where a rejected file was copied
    error_message: str | None = None
