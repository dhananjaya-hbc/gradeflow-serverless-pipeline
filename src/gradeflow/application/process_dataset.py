"""Use case: process an uploaded file end to end (validate -> clean -> stats -> store)."""

from dataclasses import dataclass, replace
from datetime import datetime

from gradeflow.application.ports import DatasetRepository, FileStorage, ProcessedDataWriter
from gradeflow.domain.cleaning import clean_rows
from gradeflow.domain.dataset import DatasetMeta, DatasetStatus
from gradeflow.domain.statistics import compute_stats
from gradeflow.domain.validation import FileValidationError, parse_rows, split_valid_rows


@dataclass(frozen=True)
class ProcessDatasetRequest:
    """Where the uploaded file is and when it arrived."""

    dataset_id: str
    bucket: str
    key: str
    uploaded_at: datetime  # timezone-aware, UTC

    @property
    def filename(self) -> str:
        return self.key.rsplit("/", 1)[-1]


class ProcessDataset:
    """Validate, clean and summarise a file, storing each result through the ports.

    Status flow: PROCESSING -> COMPLETED, or PROCESSING -> FAILED. COMPLETED is saved
    last, so a COMPLETED dataset always has its Parquet file and stats in place.
    """

    def __init__(
        self,
        storage: FileStorage,
        writer: ProcessedDataWriter,
        repository: DatasetRepository,
        min_valid_ratio: float,
        pass_mark: float,
    ) -> None:
        self._storage = storage
        self._writer = writer
        self._repository = repository
        self._min_valid_ratio = min_valid_ratio
        self._pass_mark = pass_mark

    def execute(self, request: ProcessDatasetRequest) -> DatasetMeta:
        """Process the file and return the final metadata (COMPLETED or FAILED)."""
        meta = DatasetMeta(
            dataset_id=request.dataset_id,
            filename=request.filename,
            uploaded_at=request.uploaded_at.isoformat(),
            status=DatasetStatus.PROCESSING,
        )
        self._repository.save_meta(meta)

        content = self._storage.read(request.bucket, request.key)
        try:
            rows = parse_rows(content, request.filename)
        except FileValidationError as exc:
            return self._finish(meta, DatasetStatus.FAILED, error_message=str(exc))

        valid, report = split_valid_rows(rows, current_year=request.uploaded_at.year)
        meta = replace(
            meta,
            total_rows=report.total_rows,
            valid_rows=report.valid_rows,
            invalid_rows=report.invalid_rows,
            row_errors=report.row_errors,
        )
        if not valid or report.valid_ratio < self._min_valid_ratio:
            return self._finish(
                meta,
                DatasetStatus.FAILED,
                error_message=(
                    f"only {report.valid_ratio:.1%} of rows are valid "
                    f"(minimum {self._min_valid_ratio:.1%})"
                ),
            )

        cleaned = clean_rows(valid)
        processed_key = self._writer.write(request.dataset_id, cleaned.data)
        self._repository.save_stats(
            request.dataset_id, compute_stats(cleaned.data, self._pass_mark)
        )
        return self._finish(
            meta,
            DatasetStatus.COMPLETED,
            clean_rows=len(cleaned.data),
            duplicates_removed=cleaned.duplicates_removed,
            processed_key=processed_key,
        )

    def _finish(self, meta: DatasetMeta, status: DatasetStatus, **changes: object) -> DatasetMeta:
        """Save and return the final META record."""
        final = replace(meta, status=status, **changes)
        self._repository.save_meta(final)
        return final
