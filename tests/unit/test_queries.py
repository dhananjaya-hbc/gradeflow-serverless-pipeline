"""Unit tests for the read-side use cases (the Analytics API's logic), on fakes."""

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.fakes_api import FakeReader, FakeSigner

from gradeflow.application.queries import (
    ANOMALY_METHODS,
    DATASET_ID_PATTERN,
    MAX_UPLOAD_BYTES,
    UPLOAD_EXPIRY_SECONDS,
    BadRequest,
    CreateUpload,
    GetAnomalies,
    GetDataset,
    GetStats,
    ListDatasets,
    NotFound,
    QueryError,
    check_dataset_id,
)
from gradeflow.application.read_ports import StatsRecords
from gradeflow.domain.anomalies import AnomalyMethod


def meta(dataset_id: str, uploaded_at: str = "2026-10-14T10:00:00+00:00", **extra: object) -> dict:
    return {
        "dataset_id": dataset_id,
        "filename": "results.csv",
        "uploaded_at": uploaded_at,
        "status": "COMPLETED",
        "total_rows": 904,
        "valid_rows": 894,
        "invalid_rows": 10,
        "clean_rows": 891,
        "duplicates_removed": 3,
        "anomaly_count": 9,
        "anomalies_stored": 9,
        "row_errors": ["row 46: bad id"],
        "processed_key": f"processed/{dataset_id}/data.parquet",
        "file_hash": "a" * 64,
        **extra,
    }


def anomaly(rank: int, method: str = "term_jump", **extra: object) -> dict:
    return {
        "rank": rank,
        "method": method,
        "student_id": f"S{rank:05d}",
        "severity": 40.0 - rank,
        "details": f"finding {rank}",
        **extra,
    }


# --- ListDatasets ----------------------------------------------------------------------


def test_datasets_are_listed_newest_first_as_summaries() -> None:
    reader = FakeReader(
        [
            meta("old", "2026-10-01T00:00:00+00:00"),
            meta("new", "2026-10-14T00:00:00+00:00", duplicate_of="old"),
        ]
    )

    result = ListDatasets(reader).execute()

    assert [d["dataset_id"] for d in result["datasets"]] == ["new", "old"]
    assert result["next_cursor"] is None
    assert result["datasets"][0] == {
        "dataset_id": "new",
        "filename": "results.csv",
        "uploaded_at": "2026-10-14T00:00:00+00:00",
        "status": "COMPLETED",
        "total_rows": 904,
        "valid_rows": 894,
        "clean_rows": 891,
        "anomaly_count": 9,
        "duplicate_of": "old",
        "error_message": None,
    }


def test_older_records_without_newer_fields_still_list_with_zeroes() -> None:
    """Datasets processed before the anomaly step have no anomaly_count."""
    old = {"dataset_id": "old", "filename": "a.csv", "uploaded_at": "2026-09-01T00:00:00+00:00",
           "status": "COMPLETED"}  # fmt: skip
    [summary] = ListDatasets(FakeReader([old])).execute()["datasets"]
    assert (summary["total_rows"], summary["clean_rows"], summary["anomaly_count"]) == (0, 0, 0)


def test_listing_pages_through_the_reader() -> None:
    reader = FakeReader([meta(f"d{i}", f"2026-10-{i:02d}T00:00:00+00:00") for i in range(1, 6)])
    use_case = ListDatasets(reader)

    first = use_case.execute(limit="2")
    second = use_case.execute(limit=2, cursor=first["next_cursor"])
    last = use_case.execute(limit=2, cursor=second["next_cursor"])

    assert [d["dataset_id"] for d in first["datasets"]] == ["d5", "d4"]
    assert [d["dataset_id"] for d in second["datasets"]] == ["d3", "d2"]
    assert [d["dataset_id"] for d in last["datasets"]] == ["d1"]
    assert last["next_cursor"] is None


def test_default_page_size_is_twenty_and_an_empty_cursor_means_the_first_page() -> None:
    reader = FakeReader([meta("a")])
    ListDatasets(reader).execute(limit=None, cursor="")
    assert reader.calls == [("list_datasets", 20, None)]


@pytest.mark.parametrize("limit", ["0", "101", "abc", "-1", "1.5", 0, 101])
def test_a_bad_limit_is_a_400(limit: object) -> None:
    with pytest.raises(BadRequest) as error:
        ListDatasets(FakeReader()).execute(limit=limit)
    assert (error.value.status, error.value.code) == (400, "invalid_limit")


def test_an_empty_system_lists_nothing() -> None:
    assert ListDatasets(FakeReader()).execute() == {"datasets": [], "next_cursor": None}


# --- GetDataset ------------------------------------------------------------------------


def test_a_dataset_comes_with_its_validation_report() -> None:
    result = GetDataset(FakeReader([meta("ds-1")])).execute("ds-1")

    assert result["dataset_id"] == "ds-1" and result["status"] == "COMPLETED"
    assert result["validation"] == {
        "total_rows": 904,
        "valid_rows": 894,
        "invalid_rows": 10,
        "row_errors": ["row 46: bad id"],
        "error_message": None,
    }
    assert result["cleaning"] == {
        "clean_rows": 891,
        "duplicates_removed": 3,
        "processed_key": "processed/ds-1/data.parquet",
    }
    assert result["anomalies"] == {"count": 9, "stored": 9, "ml_skipped": None}
    assert result["file_hash"] == "a" * 64


def test_a_failed_dataset_reports_why_and_where_the_file_went() -> None:
    failed = meta("ds-2", status="FAILED", error_message="missing required columns: class",
                  quarantine_key="quarantine/ds-2/a.csv")  # fmt: skip
    result = GetDataset(FakeReader([failed])).execute("ds-2")
    assert result["status"] == "FAILED"
    assert result["validation"]["error_message"] == "missing required columns: class"
    assert result["quarantine_key"] == "quarantine/ds-2/a.csv"


def test_an_unknown_dataset_is_a_404() -> None:
    with pytest.raises(NotFound) as error:
        GetDataset(FakeReader()).execute("nope")
    assert (error.value.status, error.value.code) == (404, "not_found")
    assert "nope" in error.value.message


@pytest.mark.parametrize("bad_id", ["../etc", "a b", "x" * 65, "", "a/b", "ds;1"])
def test_a_malformed_dataset_id_is_a_400_not_a_lookup(bad_id: str) -> None:
    reader = FakeReader([meta("ds-1")])
    with pytest.raises(BadRequest) as error:
        GetDataset(reader).execute(bad_id)
    assert error.value.code == "invalid_dataset_id"


# --- GetStats --------------------------------------------------------------------------

OVERALL = {"count": 891, "mean": 62.43, "median": 62.0, "std": 16.12, "min": 14.4, "max": 100.0,
           "pass_rate": 78.34, "student_count": 60,
           "top_students": [{"student_id": "S00034", "student_name": "A", "average": 93.19}],
           "bottom_students": [{"student_id": "S00018", "student_name": "B", "average": 26.28}],
           "class_averages": {"10-A": 62.11, "10-B": 62.75}}  # fmt: skip
GROUP = {"subject": "Maths", "term": "T1", "count": 60, "mean": 63.15, "median": 63.75,
         "std": 15.92, "min": 20.4, "max": 97.3, "pass_rate": 83.33}  # fmt: skip


def test_stats_for_a_completed_dataset() -> None:
    reader = FakeReader([meta("ds-1")], stats={"ds-1": StatsRecords(OVERALL, [GROUP])})

    result = GetStats(reader).execute("ds-1")

    assert result["status"] == "COMPLETED"
    assert result["overall"] == {"count": 891, "mean": 62.43, "median": 62.0, "std": 16.12,
                                 "min": 14.4, "max": 100.0, "pass_rate": 78.34}  # fmt: skip
    assert result["student_count"] == 60
    assert result["top_students"][0]["student_id"] == "S00034"
    assert result["bottom_students"][0]["student_id"] == "S00018"
    assert result["class_averages"] == {"10-A": 62.11, "10-B": 62.75}
    assert result["by_subject_term"] == [GROUP]


def test_stats_of_an_unfinished_dataset_are_empty_not_an_error() -> None:
    reader = FakeReader([meta("ds-1", status="PROCESSING")])

    result = GetStats(reader).execute("ds-1")

    assert result == {
        "dataset_id": "ds-1",
        "status": "PROCESSING",
        "overall": None,
        "student_count": None,
        "top_students": [],
        "bottom_students": [],
        "class_averages": {},
        "by_subject_term": [],
    }


def test_stats_of_an_unknown_dataset_is_a_404() -> None:
    with pytest.raises(NotFound):
        GetStats(FakeReader()).execute("nope")


# --- GetAnomalies ----------------------------------------------------------------------


def test_anomalies_come_back_in_rank_order_with_a_uniform_shape() -> None:
    reader = FakeReader(
        [meta("ds-1")],
        anomalies={"ds-1": [anomaly(1, "duplicate_id"), anomaly(2, pattern="spike", subject="Maths",
                                                                term="T2", score=77.0)]},
    )  # fmt: skip

    result = GetAnomalies(reader).execute("ds-1")

    assert result["anomaly_count"] == 9 and result["anomalies_stored"] == 9
    assert [item["rank"] for item in result["items"]] == [1, 2]
    assert result["items"][1] == {
        "rank": 2,
        "method": "term_jump",
        "student_id": "S00002",
        "student_name": None,
        "subject": "Maths",
        "term": "T2",
        "score": 77.0,
        "pattern": "spike",
        "severity": 38.0,
        "details": "finding 2",
    }
    assert set(result["items"][0]) == set(result["items"][1])  # missing fields are null


def test_anomalies_can_be_filtered_by_method() -> None:
    items = [anomaly(1, "duplicate_id"), anomaly(2), anomaly(3, "isolation_forest"), anomaly(4)]
    reader = FakeReader([meta("ds-1")], anomalies={"ds-1": items})

    result = GetAnomalies(reader).execute("ds-1", method="term_jump")

    assert [item["rank"] for item in result["items"]] == [2, 4]
    assert reader.calls[-1] == ("list_anomalies", 100, None, "term_jump")


def test_anomalies_page_and_filters_keep_pages_full() -> None:
    """Seven findings, alternating methods: the four term jumps (ranks 1, 3, 5, 7) come back
    in full pages of two, and the last page says there is nothing more."""
    items = [anomaly(n, "term_jump" if n % 2 else "isolation_forest") for n in range(1, 8)]
    reader = FakeReader([meta("ds-1")], anomalies={"ds-1": items})
    use_case = GetAnomalies(reader)

    first = use_case.execute("ds-1", method="term_jump", limit=2)
    second = use_case.execute("ds-1", method="term_jump", limit=2, cursor=first["next_cursor"])

    assert [i["rank"] for i in first["items"]] == [1, 3]
    assert first["next_cursor"] is not None
    assert [i["rank"] for i in second["items"]] == [5, 7]
    assert second["next_cursor"] is None  # a client stops here


def test_a_cursor_past_the_end_returns_an_empty_page() -> None:
    reader = FakeReader([meta("ds-1")], anomalies={"ds-1": [anomaly(1), anomaly(2)]})
    result = GetAnomalies(reader).execute("ds-1", cursor="2")
    assert result["items"] == [] and result["next_cursor"] is None


def test_an_unknown_method_is_a_400_listing_the_valid_ones() -> None:
    with pytest.raises(BadRequest) as error:
        GetAnomalies(FakeReader([meta("ds-1")])).execute("ds-1", method="telepathy")
    assert error.value.code == "invalid_method"
    assert "term_jump" in error.value.message


def test_anomaly_limit_goes_up_to_500_and_no_further() -> None:
    reader = FakeReader([meta("ds-1")])
    GetAnomalies(reader).execute("ds-1", limit="500")
    with pytest.raises(BadRequest):
        GetAnomalies(reader).execute("ds-1", limit="501")


def test_anomalies_of_an_unknown_dataset_is_a_404() -> None:
    with pytest.raises(NotFound):
        GetAnomalies(FakeReader()).execute("nope")


def test_the_method_names_match_the_detectors() -> None:
    """The API cannot import the detectors (they need pandas), so this keeps the copy honest."""
    assert {method.value for method in AnomalyMethod} == ANOMALY_METHODS


# --- CreateUpload ----------------------------------------------------------------------


def make_upload(signer: FakeSigner | None = None) -> tuple[CreateUpload, FakeSigner]:
    signer = signer or FakeSigner()
    clock = lambda: datetime(2026, 10, 15, 9, 30, tzinfo=UTC)  # noqa: E731
    return CreateUpload(signer, clock=clock, suffix=lambda: "1a2b3c4d"), signer


def test_an_upload_gets_a_server_chosen_dataset_id_and_a_signed_form() -> None:
    use_case, signer = make_upload()

    result = use_case.execute("results.csv")

    assert result == {
        "dataset_id": "ds-20261015-1a2b3c4d",
        "key": "uploads/ds-20261015-1a2b3c4d/results.csv",
        "upload": {
            "method": "POST",
            "url": "https://raw-bucket.s3.amazonaws.com/",
            "fields": {"key": "uploads/ds-20261015-1a2b3c4d/results.csv", "policy": "signed"},
        },
        "max_bytes": 50 * 1024 * 1024,
        "expires_in": 900,
    }
    assert signer.requests == [
        ("uploads/ds-20261015-1a2b3c4d/results.csv", MAX_UPLOAD_BYTES, UPLOAD_EXPIRY_SECONDS)
    ]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("results.csv", "results.csv"),
        ("my results (final).csv".replace("(final)", "final"), "my results final.csv"),
        ("DATA.JSON", "DATA.JSON"),
        ("Results.CSV", "Results.CSV"),
        ("folder/sub\\results.csv", "results.csv"),  # a folder part is dropped
        ("../../etc/passwd.csv", "passwd.csv"),
    ],
)
def test_filenames_are_cleaned_and_cannot_escape_the_dataset_folder(
    given: str, expected: str
) -> None:
    use_case, _ = make_upload()
    result = use_case.execute(given)
    assert result["key"] == f"uploads/ds-20261015-1a2b3c4d/{expected}"
    assert ".." not in result["key"]


@pytest.mark.parametrize(
    "filename",
    [None, "", "   ", 123, ["a.csv"], ".csv", "a/b/", "res<ul>ts.csv", "a" * 101 + ".csv"],
)
def test_a_bad_filename_is_a_400(filename: object) -> None:
    use_case, signer = make_upload()
    with pytest.raises(BadRequest) as error:
        use_case.execute(filename)
    assert error.value.code == "invalid_filename"
    assert signer.requests == []  # nothing was signed


@pytest.mark.parametrize("filename", ["results.xlsx", "results.txt", "results", "results.csv.exe"])
def test_only_csv_and_json_can_be_uploaded(filename: str) -> None:
    use_case, signer = make_upload()
    with pytest.raises(BadRequest) as error:
        use_case.execute(filename)
    assert error.value.code == "unsupported_file_type"
    assert signer.requests == []


def test_generated_ids_are_unique_and_valid_for_the_other_endpoints() -> None:
    use_case = CreateUpload(FakeSigner())

    ids = {use_case.execute("a.csv")["dataset_id"] for _ in range(50)}

    assert len(ids) == 50
    assert all(re.fullmatch(r"ds-\d{8}-[0-9a-f]{8}", i) for i in ids)
    assert all(DATASET_ID_PATTERN.fullmatch(i) for i in ids)
    assert all(check_dataset_id(i) == i for i in ids)


def test_query_errors_carry_their_http_status() -> None:
    assert (BadRequest("x").status, NotFound("x").status) == (400, 404)
    assert issubclass(BadRequest, QueryError) and issubclass(NotFound, QueryError)


# --- the API Lambda must not need pandas -----------------------------------------------

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.mark.parametrize(
    "module", ["gradeflow.application.queries", "gradeflow.application.read_ports"]
)
def test_read_side_modules_import_no_heavy_libraries(module: str) -> None:
    code = (
        f"import {module}, sys, json; "
        "print(json.dumps(sorted(m for m in ('pandas', 'numpy', 'sklearn', 'boto3', 'pyarrow') "
        "if m in sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    assert json.loads(result.stdout) == [], f"{module} pulled in a heavy library"
