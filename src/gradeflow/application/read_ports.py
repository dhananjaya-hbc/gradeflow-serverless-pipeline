"""Ports for the read side (the Analytics API): what the query use cases need.

Kept apart from `ports.py` on purpose: that module imports pandas, and the API Lambda has no
pandas. Everything here uses plain Python types: adapters turn DynamoDB's Decimal numbers
into int and float before anything is returned.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Page:
    """One page of results. `next_cursor` is opaque; None means there is nothing more."""

    items: list[dict[str, Any]]
    next_cursor: str | None = None


@dataclass(frozen=True)
class StatsRecords:
    """The stored statistics of a dataset (items without their DynamoDB keys)."""

    overall: dict[str, Any] | None  # the STATS#OVERALL item, None if there is none
    groups: list[dict[str, Any]]  # the STATS#SUBJECT# items, sorted by subject then term


@dataclass(frozen=True)
class SignedUpload:
    """A pre-signed S3 form: POST `fields` and then the file (as the last field) to `url`."""

    url: str
    fields: dict[str, str]


class DatasetReader(Protocol):
    """Read-only access to dataset records."""

    def list_datasets(self, limit: int, cursor: str | None) -> Page:
        """META records, newest upload first. `cursor` comes from a previous page."""
        ...

    def get_meta(self, dataset_id: str) -> dict[str, Any] | None:
        """The META record, or None if the dataset does not exist."""
        ...

    def get_stats(self, dataset_id: str) -> StatsRecords:
        """The stored statistics (empty when the dataset has none yet)."""
        ...

    def list_anomalies(
        self, dataset_id: str, limit: int, cursor: str | None, method: str | None
    ) -> Page:
        """ANOMALY# records in rank order (most important first).

        With `method`, only findings of that method are returned. A page holds up to `limit`
        findings, so filtering never makes a page come back short while more exist.
        """
        ...


class UploadSigner(Protocol):
    """Creates pre-signed upload forms for the raw bucket."""

    def create_upload(self, key: str, max_bytes: int, expires_in: int) -> SignedUpload:
        """A form that lets the holder upload ONE object at exactly `key`, at most
        `max_bytes` large, within `expires_in` seconds."""
        ...
