"""The workflow steps as use cases. Each Lambda handler runs exactly one of these.

Trigger ─▶ Validate ─▶ Clean ─▶ Stats ─▶ Store          (happy path)
              └─ invalid file / error ─▶ Reject          (failure branch)
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from gradeflow.application.pipeline_state import CleaningOutcome, PipelineState, ValidationOutcome
from gradeflow.application.ports import (
    DatasetArtifacts,
    DatasetRepository,
    FileQuarantine,
    FileStorage,
    WorkflowStarter,
)
from gradeflow.domain.cleaning import clean_rows
from gradeflow.domain.dataset import DatasetMeta, DatasetStatus
from gradeflow.domain.fingerprint import sha256_hex
from gradeflow.domain.statistics import compute_stats, stats_from_dict, stats_to_dict
from gradeflow.domain.validation import FileValidationError, parse_rows, split_valid_rows

MAX_ERROR_MESSAGE = 500  # keep crash messages from Step Functions small
_EXECUTION_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


# --- Trigger -------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerRequest:
    """One uploaded file, as announced by S3."""

    dataset_id: str
    bucket: str
    key: str
    uploaded_at: datetime  # timezone-aware, UTC

    @property
    def filename(self) -> str:
        return self.key.rsplit("/", 1)[-1]


class TriggerStatus(StrEnum):
    STARTED = "STARTED"  # a new workflow run was started
    DUPLICATE = "DUPLICATE"  # identical file already uploaded as another dataset
    ALREADY_STARTED = "ALREADY_STARTED"  # redelivered message: this run exists already


class TriggerPipeline:
    """Fingerprint the upload, skip duplicates, otherwise start the workflow."""

    def __init__(
        self, storage: FileStorage, repository: DatasetRepository, workflow: WorkflowStarter
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._workflow = workflow

    def execute(self, request: TriggerRequest) -> TriggerStatus:
        content = self._storage.read(request.bucket, request.key)
        file_hash = sha256_hex(content)
        uploaded_at = request.uploaded_at.isoformat()
        base = DatasetMeta(
            dataset_id=request.dataset_id,
            filename=request.filename,
            uploaded_at=uploaded_at,
            status=DatasetStatus.PROCESSING,
            file_hash=file_hash,
        )

        owner = self._repository.claim_file_hash(file_hash, request.dataset_id)
        if owner is not None and owner != request.dataset_id:
            self._repository.save_meta(
                replace(
                    base,
                    status=DatasetStatus.DUPLICATE,
                    duplicate_of=owner,
                    error_message=f"identical file was already uploaded as dataset '{owner}'",
                )
            )
            return TriggerStatus.DUPLICATE

        state = PipelineState(
            request.dataset_id, request.bucket, request.key, uploaded_at, file_hash
        )
        if not self._workflow.start(execution_name(state), state.to_dict()):
            return TriggerStatus.ALREADY_STARTED
        # After starting, and only if absent: a finished run's COMPLETED must never be
        # overwritten by PROCESSING, and re-processing keeps showing the previous result.
        self._repository.create_meta_if_absent(base)
        return TriggerStatus.STARTED


def execution_name(state: PipelineState) -> str:
    """Unique per upload event, so an SQS redelivery maps to the same run but a later
    re-upload starts a new one. Step Functions names allow only letters, digits, - and _.
    """
    safe_id = _EXECUTION_NAME_UNSAFE.sub("_", state.dataset_id)[:40]
    stamp = state.uploaded_at_datetime.strftime("%Y%m%dT%H%M%S%f")
    return f"{safe_id}-{state.file_hash[:12]}-{stamp}"


# --- Validate ------------------------------------------------------------------------


class ValidateStep:
    """Parse and validate the raw file; save the valid rows for the Clean step.

    A bad file is a normal outcome (`valid=False`), not an exception: raising would make
    Step Functions retry something that cannot succeed.
    """

    def __init__(
        self, storage: FileStorage, artifacts: DatasetArtifacts, min_valid_ratio: float
    ) -> None:
        self._storage = storage
        self._artifacts = artifacts
        self._min_valid_ratio = min_valid_ratio

    def execute(self, state: PipelineState) -> ValidationOutcome:
        content = self._storage.read(state.bucket, state.key)
        try:
            rows = parse_rows(content, state.filename)
        except FileValidationError as exc:
            return ValidationOutcome(valid=False, error_message=str(exc))

        valid, report = split_valid_rows(rows, current_year=state.uploaded_at_datetime.year)
        counts = {
            "total_rows": report.total_rows,
            "valid_rows": report.valid_rows,
            "invalid_rows": report.invalid_rows,
            "row_errors": report.row_errors,
        }
        if not valid or report.valid_ratio < self._min_valid_ratio:
            message = (
                f"only {report.valid_ratio:.1%} of rows are valid "
                f"(minimum {self._min_valid_ratio:.1%})"
            )
            return ValidationOutcome(valid=False, error_message=message, **counts)
        valid_key = self._artifacts.save_valid_rows(state.dataset_id, valid)
        return ValidationOutcome(valid=True, valid_key=valid_key, **counts)


# --- Clean ---------------------------------------------------------------------------


class CleanStep:
    """Clean the valid rows and save the final Parquet file."""

    def __init__(self, artifacts: DatasetArtifacts) -> None:
        self._artifacts = artifacts

    def execute(self, state: PipelineState) -> CleaningOutcome:
        assert state.validation is not None and state.validation.valid_key is not None
        rows = self._artifacts.load_valid_rows(state.validation.valid_key)
        cleaned = clean_rows(rows)
        processed_key = self._artifacts.save_clean_data(state.dataset_id, cleaned.data)
        return CleaningOutcome(
            clean_rows=len(cleaned.data),
            duplicates_removed=cleaned.duplicates_removed,
            processed_key=processed_key,
        )


# --- Stats ---------------------------------------------------------------------------


class StatsStep:
    """Compute statistics from the cleaned data. Returns them as a JSON-ready dict."""

    def __init__(self, artifacts: DatasetArtifacts, pass_mark: float) -> None:
        self._artifacts = artifacts
        self._pass_mark = pass_mark

    def execute(self, state: PipelineState) -> dict:
        assert state.cleaning is not None
        data = self._artifacts.load_clean_data(state.cleaning.processed_key)
        return stats_to_dict(compute_stats(data, self._pass_mark))


# --- Store ---------------------------------------------------------------------------


class StoreStep:
    """Write the statistics, then mark the dataset COMPLETED (last, so it means "all there")."""

    def __init__(self, repository: DatasetRepository) -> None:
        self._repository = repository

    def execute(self, state: PipelineState) -> DatasetMeta:
        assert state.validation and state.cleaning and state.stats is not None
        # Re-processing an ID must not leave stats of an earlier upload behind.
        self._repository.delete_stats(state.dataset_id)
        self._repository.save_stats(state.dataset_id, stats_from_dict(state.stats))
        meta = DatasetMeta(
            dataset_id=state.dataset_id,
            filename=state.filename,
            uploaded_at=state.uploaded_at,
            status=DatasetStatus.COMPLETED,
            total_rows=state.validation.total_rows,
            valid_rows=state.validation.valid_rows,
            invalid_rows=state.validation.invalid_rows,
            row_errors=state.validation.row_errors,
            clean_rows=state.cleaning.clean_rows,
            duplicates_removed=state.cleaning.duplicates_removed,
            processed_key=state.cleaning.processed_key,
            file_hash=state.file_hash,
        )
        self._repository.save_meta(meta)
        return meta


# --- Reject (failure branch) ---------------------------------------------------------


class RejectDataset:
    """Mark the dataset FAILED. A bad file is also quarantined; a crashed run is not.

    The state machine reaches this step in two ways:
    - Validate said `valid=False`: the file is bad, so keep a copy in quarantine.
    - A step crashed after its retries (`error` is set): the file may be fine, so no copy.
    """

    def __init__(self, quarantine: FileQuarantine, repository: DatasetRepository) -> None:
        self._quarantine = quarantine
        self._repository = repository

    def execute(self, state: PipelineState) -> DatasetMeta:
        validation = state.validation or ValidationOutcome(valid=False)
        quarantine_key = None
        if state.error is None:
            quarantine_key = self._quarantine.quarantine(state.bucket, state.key, state.dataset_id)
        meta = DatasetMeta(
            dataset_id=state.dataset_id,
            filename=state.filename,
            uploaded_at=state.uploaded_at,
            status=DatasetStatus.FAILED,
            total_rows=validation.total_rows,
            valid_rows=validation.valid_rows,
            invalid_rows=validation.invalid_rows,
            row_errors=validation.row_errors,
            file_hash=state.file_hash,
            quarantine_key=quarantine_key,
            error_message=_failure_message(state, validation),
        )
        self._repository.save_meta(meta)
        return meta


def _failure_message(state: PipelineState, validation: ValidationOutcome) -> str:
    if state.error is not None:
        detail = f"{state.error.get('Error', 'Error')}: {state.error.get('Cause', '')}"
        return f"processing failed ({detail})"[:MAX_ERROR_MESSAGE]
    return validation.error_message or "file rejected"
