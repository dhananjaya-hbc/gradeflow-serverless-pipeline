"""In-memory stand-ins for the ports, shared by the unit tests (no AWS involved)."""

from typing import Any

import pandas as pd

from gradeflow.domain.dataset import DatasetMeta
from gradeflow.domain.statistics import DatasetStats
from gradeflow.domain.validation import Row


class FakeStorage:
    """Stands in for S3: a dict of (bucket, key) -> bytes."""

    def __init__(self, files: dict[tuple[str, str], bytes] | None = None) -> None:
        self.files = files or {}

    def read(self, bucket: str, key: str) -> bytes:
        return self.files[(bucket, key)]


class FakeArtifacts:
    """Stands in for the work/processed files of the processed bucket."""

    def __init__(self) -> None:
        self.valid_rows: dict[str, list[Row]] = {}
        self.clean_data: dict[str, pd.DataFrame] = {}

    def save_valid_rows(self, dataset_id: str, rows: list[Row]) -> str:
        key = f"work/{dataset_id}/valid.jsonl"
        self.valid_rows[key] = list(rows)
        return key

    def load_valid_rows(self, key: str) -> list[Row]:
        return self.valid_rows[key]

    def save_clean_data(self, dataset_id: str, data: pd.DataFrame) -> str:
        key = f"processed/{dataset_id}/data.parquet"
        self.clean_data[key] = data
        return key

    def load_clean_data(self, key: str) -> pd.DataFrame:
        return self.clean_data[key]


class FakeQuarantine:
    """Stands in for the quarantine bucket: remembers which files were copied."""

    def __init__(self) -> None:
        self.copied: list[tuple[str, str, str]] = []  # (bucket, key, dataset_id)

    def quarantine(self, bucket: str, key: str, dataset_id: str) -> str:
        self.copied.append((bucket, key, dataset_id))
        return f"quarantine/{dataset_id}/{key.rsplit('/', 1)[-1]}"


class FakeRepository:
    """Stands in for DynamoDB: records every call, in order."""

    def __init__(self) -> None:
        self.metas: list[DatasetMeta] = []  # every save, in order
        self.current: dict[str, DatasetMeta] = {}  # latest META per dataset
        self.stats: dict[str, DatasetStats] = {}
        self.claims: dict[str, str] = {}  # sha256 -> owning dataset_id
        self.calls: list[str] = []

    def save_meta(self, meta: DatasetMeta) -> None:
        self.metas.append(meta)
        self.current[meta.dataset_id] = meta
        self.calls.append(f"meta:{meta.status}")

    def create_meta_if_absent(self, meta: DatasetMeta) -> bool:
        self.calls.append(f"create_meta:{meta.status}")
        if meta.dataset_id in self.current:
            return False
        self.metas.append(meta)
        self.current[meta.dataset_id] = meta
        return True

    def save_stats(self, dataset_id: str, stats: DatasetStats) -> None:
        self.stats[dataset_id] = stats
        self.calls.append("stats")

    def delete_stats(self, dataset_id: str) -> None:
        self.stats.pop(dataset_id, None)
        self.calls.append("delete_stats")

    def claim_file_hash(self, sha256: str, dataset_id: str) -> str | None:
        self.calls.append("claim")
        owner = self.claims.get(sha256)
        if owner is None:
            self.claims[sha256] = dataset_id
        return owner


class FakeWorkflow:
    """Stands in for Step Functions: remembers started executions; names are unique."""

    def __init__(self) -> None:
        self.started: list[tuple[str, dict[str, Any]]] = []

    def start(self, name: str, payload: dict[str, Any]) -> bool:
        if any(name == existing for existing, _ in self.started):
            return False
        self.started.append((name, payload))
        return True
