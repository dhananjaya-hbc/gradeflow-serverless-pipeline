"""Ports: interfaces the use cases need from the outside world.

Infrastructure adapters (S3, DynamoDB) implement these; tests use in-memory fakes.
"""

from typing import Protocol

import pandas as pd

from gradeflow.domain.dataset import DatasetMeta
from gradeflow.domain.statistics import DatasetStats


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


class ProcessedDataWriter(Protocol):
    """Stores cleaned data for later analysis."""

    def write(self, dataset_id: str, data: pd.DataFrame) -> str:
        """Save the cleaned data and return the key it was written to."""
        ...


class DatasetRepository(Protocol):
    """Persists dataset metadata and statistics."""

    def save_meta(self, meta: DatasetMeta) -> None:
        """Create or overwrite the META record for a dataset."""
        ...

    def save_stats(self, dataset_id: str, stats: DatasetStats) -> None:
        """Create or overwrite the statistics records for a dataset."""
        ...

    def delete_stats(self, dataset_id: str) -> None:
        """Remove all statistics records of a dataset (before re-processing it)."""
        ...

    def claim_file_hash(self, sha256: str, dataset_id: str) -> str | None:
        """Atomically record that `dataset_id` owns this file content.

        Returns None if the claim succeeded, or the owning dataset_id if the content was
        claimed before. The owner can be `dataset_id` itself (a retry of the same upload).
        """
        ...
