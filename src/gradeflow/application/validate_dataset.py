"""Use case: validate an uploaded file and record the outcome as dataset metadata."""

from dataclasses import dataclass
from datetime import datetime

from gradeflow.application.ports import DatasetRepository, FileStorage
from gradeflow.domain.dataset import DatasetMeta, DatasetStatus
from gradeflow.domain.validation import FileValidationError, parse_rows, validate_rows


@dataclass(frozen=True)
class ValidateDatasetRequest:
    """Where the uploaded file is and when it arrived."""

    dataset_id: str
    bucket: str
    key: str
    uploaded_at: datetime  # timezone-aware, UTC

    @property
    def filename(self) -> str:
        return self.key.rsplit("/", 1)[-1]


class ValidateDataset:
    """Read a file, validate it, and save a META record with the result."""

    def __init__(
        self, storage: FileStorage, repository: DatasetRepository, min_valid_ratio: float
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._min_valid_ratio = min_valid_ratio

    def execute(self, request: ValidateDatasetRequest) -> DatasetMeta:
        """Validate the file and persist its metadata. Returns the saved metadata."""
        meta = self._validate(request)
        self._repository.save_meta(meta)
        return meta

    def _validate(self, request: ValidateDatasetRequest) -> DatasetMeta:
        base = {
            "dataset_id": request.dataset_id,
            "filename": request.filename,
            "uploaded_at": request.uploaded_at.isoformat(),
        }
        content = self._storage.read(request.bucket, request.key)
        try:
            rows = parse_rows(content, request.filename)
        except FileValidationError as exc:
            return DatasetMeta(**base, status=DatasetStatus.FAILED, error_message=str(exc))

        report = validate_rows(rows, current_year=request.uploaded_at.year)
        passed = report.valid_ratio >= self._min_valid_ratio
        return DatasetMeta(
            **base,
            status=DatasetStatus.COMPLETED if passed else DatasetStatus.FAILED,
            total_rows=report.total_rows,
            valid_rows=report.valid_rows,
            invalid_rows=report.invalid_rows,
            row_errors=report.row_errors,
            error_message=None
            if passed
            else (
                f"only {report.valid_ratio:.1%} of rows are valid "
                f"(minimum {self._min_valid_ratio:.1%})"
            ),
        )
