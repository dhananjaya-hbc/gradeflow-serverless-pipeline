"""In-memory stand-ins for the read-side ports (no AWS involved)."""

from typing import Any

from gradeflow.application.read_ports import Page, SignedUpload, StatsRecords


class FakeReader:
    """A DatasetReader over dicts. Paging follows the port's contract: opaque cursors,
    newest dataset first, anomalies in rank order, and a method filter that fills the page."""

    def __init__(
        self,
        metas: list[dict[str, Any]] | None = None,
        stats: dict[str, StatsRecords] | None = None,
        anomalies: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.metas = {meta["dataset_id"]: meta for meta in (metas or [])}
        self.stats = stats or {}
        self.anomalies = anomalies or {}
        self.calls: list[tuple] = []

    def list_datasets(self, limit: int, cursor: str | None) -> Page:
        self.calls.append(("list_datasets", limit, cursor))
        ordered = sorted(self.metas.values(), key=lambda m: m["uploaded_at"], reverse=True)
        start = int(cursor) if cursor else 0
        more = start + limit < len(ordered)
        return Page(ordered[start : start + limit], str(start + limit) if more else None)

    def get_meta(self, dataset_id: str) -> dict[str, Any] | None:
        return self.metas.get(dataset_id)

    def get_stats(self, dataset_id: str) -> StatsRecords:
        return self.stats.get(dataset_id, StatsRecords(None, []))

    def list_anomalies(
        self, dataset_id: str, limit: int, cursor: str | None, method: str | None
    ) -> Page:
        self.calls.append(("list_anomalies", limit, cursor, method))
        after = int(cursor) if cursor else 0
        matching = [
            a
            for a in self.anomalies.get(dataset_id, [])
            if a["rank"] > after and (method is None or a["method"] == method)
        ]
        page = matching[:limit]
        return Page(page, str(page[-1]["rank"]) if len(matching) > limit else None)


class FakeSigner:
    """An UploadSigner that remembers what it was asked to sign."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, int, int]] = []

    def create_upload(self, key: str, max_bytes: int, expires_in: int) -> SignedUpload:
        self.requests.append((key, max_bytes, expires_in))
        return SignedUpload(
            url="https://raw-bucket.s3.amazonaws.com/", fields={"key": key, "policy": "signed"}
        )
