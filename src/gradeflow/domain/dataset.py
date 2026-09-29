"""Dataset entity: the metadata we track for every uploaded file."""

from dataclasses import dataclass
from enum import StrEnum


class DatasetStatus(StrEnum):
    """Lifecycle of an uploaded dataset."""

    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


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
    error_message: str | None = None
