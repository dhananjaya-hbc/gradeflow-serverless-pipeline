"""The state that travels through the Step Functions workflow, as plain JSON dicts.

Each step receives the whole state and returns one new section (`validation`, `cleaning`,
`stats`), which the state machine merges in. Large data never travels in the state (the
limit is 256 KB): steps pass S3 keys instead.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ValidationOutcome:
    """Result of the Validate step. `valid` is False for a bad file; that is not an error."""

    valid: bool
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    row_errors: tuple[str, ...] = ()
    valid_key: str | None = None  # where the valid rows were saved (only when valid)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "row_errors": list(self.row_errors)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ValidationOutcome":
        return cls(**{**data, "row_errors": tuple(data.get("row_errors", ()))})


@dataclass(frozen=True)
class CleaningOutcome:
    """Result of the Clean step."""

    clean_rows: int
    duplicates_removed: int
    processed_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CleaningOutcome":
        return cls(**data)


@dataclass(frozen=True)
class PipelineState:
    """Everything known about one run. Sections are None until their step has run."""

    dataset_id: str
    bucket: str
    key: str
    uploaded_at: str  # ISO 8601, UTC (the S3 event time)
    file_hash: str
    validation: ValidationOutcome | None = None
    cleaning: CleaningOutcome | None = None
    stats: dict[str, Any] | None = None  # stats_to_dict() output
    error: dict[str, Any] | None = None  # set by the state machine's Catch: {Error, Cause}

    @property
    def filename(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    @property
    def uploaded_at_datetime(self) -> datetime:
        return datetime.fromisoformat(self.uploaded_at)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict; sections that have not run yet are left out."""
        data: dict[str, Any] = {
            "dataset_id": self.dataset_id,
            "bucket": self.bucket,
            "key": self.key,
            "uploaded_at": self.uploaded_at,
            "file_hash": self.file_hash,
        }
        if self.validation is not None:
            data["validation"] = self.validation.to_dict()
        if self.cleaning is not None:
            data["cleaning"] = self.cleaning.to_dict()
        if self.stats is not None:
            data["stats"] = self.stats
        if self.error is not None:
            data["error"] = self.error
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineState":
        validation, cleaning = data.get("validation"), data.get("cleaning")
        return cls(
            dataset_id=data["dataset_id"],
            bucket=data["bucket"],
            key=data["key"],
            uploaded_at=data["uploaded_at"],
            file_hash=data["file_hash"],
            validation=ValidationOutcome.from_dict(validation) if validation else None,
            cleaning=CleaningOutcome.from_dict(cleaning) if cleaning else None,
            stats=data.get("stats"),
            error=data.get("error"),
        )
