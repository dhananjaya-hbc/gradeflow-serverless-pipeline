"""Ports: interfaces the use cases need from the outside world.

Infrastructure adapters (S3, DynamoDB) implement these; tests use in-memory fakes.
"""

from typing import Protocol

from gradeflow.domain.dataset import DatasetMeta


class FileStorage(Protocol):
    """Reads uploaded files."""

    def read(self, bucket: str, key: str) -> bytes:
        """Return the full content of the file at bucket/key."""
        ...


class DatasetRepository(Protocol):
    """Persists dataset metadata."""

    def save_meta(self, meta: DatasetMeta) -> None:
        """Create or overwrite the META record for a dataset."""
        ...
