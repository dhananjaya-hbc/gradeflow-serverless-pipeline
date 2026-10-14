"""Use case: process an uploaded file end to end (validate -> clean -> stats -> store)."""

from dataclasses import dataclass, replace
from datetime import datetime

from gradeflow.application.ports import (
    DatasetRepository,
    FileQuarantine,
    FileStorage,
    ProcessedDataWriter,
)
from gradeflow.domain.cleaning import clean_rows
from gradeflow.domain.dataset import DatasetMeta, DatasetStatus
from gradeflow.domain.fingerprint import sha256_hex
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

    Status flow: PROCESSING -> COMPLETED, FAILED (file also quarantined) or DUPLICATE
    (same bytes as an earlier dataset, nothing else done). COMPLETED is saved last, so a
    COMPLETED dataset always has its Parquet file and stats in place.

    Safe to run again for the same upload (SQS delivers at least once): the file hash is
    claimed atomically, a retry finds its own claim and simply processes again.
    """

    def __init__(
        self,
        storage: FileStorage,
        writer: ProcessedDataWriter,
        quarantine: FileQuarantine,
        repository: DatasetRepository,
        min_valid_ratio: float,
        pass_mark: float,
    ) -> None:
        self._storage = storage
        self._writer = writer
        self._quarantine = quarantine
        self._repository = repository
        self._min_valid_ratio = min_valid_ratio
        self._pass_mark = pass_mark

    def execute(self, request: ProcessDatasetRequest) -> DatasetMeta:
        """Process the file and return the final metadata (COMPLETED, FAILED or DUPLICATE)."""
        meta = DatasetMeta(
            dataset_id=request.dataset_id,
            filename=request.filename,
            uploaded_at=request.uploaded_at.isoformat(),
            status=DatasetStatus.PROCESSING,
        )
        self._repository.save_meta(meta)

        content = self._storage.read(request.bucket, request.key)
        file_hash = sha256_hex(content)
        meta = replace(meta, file_hash=file_hash)

        owner = self._repository.claim_file_hash(file_hash, request.dataset_id)
        if owner is not None and owner != request.dataset_id:
            return self._finish(
                meta,
                DatasetStatus.DUPLICATE,
                duplicate_of=owner,
                error_message=f"identical file was already uploaded as dataset '{owner}'",
            )

        try:
            rows = parse_rows(content, request.filename)
        except FileValidationError as exc:
            return self._reject(meta, request, str(exc))

        valid, report = split_valid_rows(rows, current_year=request.uploaded_at.year)
        meta = replace(
            meta,
            total_rows=report.total_rows,
            valid_rows=report.valid_rows,
            invalid_rows=report.invalid_rows,
            row_errors=report.row_errors,
        )
        if not valid or report.valid_ratio < self._min_valid_ratio:
            return self._reject(
                meta,
                request,
                f"only {report.valid_ratio:.1%} of rows are valid "
                f"(minimum {self._min_valid_ratio:.1%})",
            )

        cleaned = clean_rows(valid)
        processed_key = self._writer.write(request.dataset_id, cleaned.data)
        # Re-processing an ID must not leave stats of an earlier upload behind.
        self._repository.delete_stats(request.dataset_id)
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

    def _reject(
        self, meta: DatasetMeta, request: ProcessDatasetRequest, error_message: str
    ) -> DatasetMeta:
        """Quarantine a rejected file and finish as FAILED."""
        quarantine_key = self._quarantine.quarantine(
            request.bucket, request.key, request.dataset_id
        )
        return self._finish(
            meta,
            DatasetStatus.FAILED,
            quarantine_key=quarantine_key,
            error_message=error_message,
        )

    def _finish(self, meta: DatasetMeta, status: DatasetStatus, **changes: object) -> DatasetMeta:
        """Save and return the final META record."""
        final = replace(meta, status=status, **changes)
        self._repository.save_meta(final)
        return final
