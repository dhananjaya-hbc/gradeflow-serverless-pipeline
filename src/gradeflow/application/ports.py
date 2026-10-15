"""Ports: interfaces the use cases need from the outside world.

Infrastructure adapters (S3, DynamoDB, Step Functions) implement these; tests use in-memory
fakes.
"""

from typing import Any, Protocol

import pandas as pd

from gradeflow.domain.anomalies import Anomaly
from gradeflow.domain.dataset import DatasetMeta
from gradeflow.domain.statistics import DatasetStats
from gradeflow.domain.validation import Row


class FileStorage(Protocol):
    """Reads uploaded files."""

    def read(self, bucket: str, key: str) -> bytes:
        """Return the full content of the file at bucket/key."""
        ...


class FileQuarantine(Protocol):
    """Keeps a copy of files that were rejected, for later inspection."""

    def quarantine(self, bucket: str, key: str, dataset_id: str) -> str:
        """Copy the rejected file out of the raw bucket and return its quarantine key."""
        ...


class DatasetArtifacts(Protocol):
    """Files the pipeline steps hand to each other (Step Functions payloads are too small)."""

    def save_valid_rows(self, dataset_id: str, rows: list[Row]) -> str:
        """Store the validated (not yet cleaned) rows and return their key."""
        ...

    def load_valid_rows(self, key: str) -> list[Row]:
        """Load rows saved by save_valid_rows."""
        ...

    def save_clean_data(self, dataset_id: str, data: pd.DataFrame) -> str:
        """Store the final cleaned data (Parquet) and return its key."""
        ...

    def load_clean_data(self, key: str) -> pd.DataFrame:
        """Load data saved by save_clean_data."""
        ...

    def save_anomalies(self, dataset_id: str, anomalies: list[Anomaly]) -> str:
        """Store every finding of the Anomalies step (in order) and return the key."""
        ...

    def load_anomalies(self, key: str) -> list[Anomaly]:
        """Load findings saved by save_anomalies, in the same order."""
        ...


class DatasetRepository(Protocol):
    """Persists dataset metadata, statistics and file-hash claims."""

    def save_meta(self, meta: DatasetMeta) -> None:
        """Create or overwrite the META record for a dataset."""
        ...

    def create_meta_if_absent(self, meta: DatasetMeta) -> bool:
        """Create the META record only if the dataset has none yet. True if created."""
        ...

    def save_stats(self, dataset_id: str, stats: DatasetStats) -> None:
        """Create or overwrite the statistics records for a dataset."""
        ...

    def delete_stats(self, dataset_id: str) -> None:
        """Remove all statistics records of a dataset (before re-processing it)."""
        ...

    def save_anomalies(self, dataset_id: str, anomalies: list[Anomaly]) -> None:
        """Write the findings as ANOMALY# records, numbered in the order given."""
        ...

    def delete_anomalies(self, dataset_id: str) -> None:
        """Remove all anomaly records of a dataset (before re-processing it)."""
        ...

    def claim_file_hash(self, sha256: str, dataset_id: str) -> str | None:
        """Atomically record that `dataset_id` owns this file content.

        Returns None if the claim succeeded, or the owning dataset_id if the content was
        claimed before. The owner can be `dataset_id` itself (a retry of the same upload).
        """
        ...


class WorkflowStarter(Protocol):
    """Starts the processing workflow for one upload."""

    def start(self, name: str, payload: dict[str, Any]) -> bool:
        """Start an execution called `name`. False if one with that name already exists."""
        ...
