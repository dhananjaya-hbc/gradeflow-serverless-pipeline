"""Unit tests for the Analytics API handler: routing, parameters, errors and headers."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.unit.fakes_api import FakeReader, FakeSigner

from gradeflow.application.queries import (
    CreateUpload,
    GetAnomalies,
    GetDataset,
    GetStats,
    ListDatasets,
)
from gradeflow.application.read_ports import Page, StatsRecords
from gradeflow.entrypoints import api_handler
from gradeflow.entrypoints.api_handler import MAX_BODY_BYTES, Api

META = {
    "dataset_id": "ds-1",
    "filename": "results.csv",
    "uploaded_at": "2026-10-14T10:00:00+00:00",
    "status": "COMPLETED",
    "total_rows": 904,
    "valid_rows": 894,
    "clean_rows": 891,
    "anomaly_count": 2,
    "anomalies_stored": 2,
}
OVERALL = {"count": 891, "mean": 62.43, "median": 62, "std": 16.12, "min": 14.4, "max": 100,
           "pass_rate": 78.34, "student_count": 60, "top_students": [], "bottom_students": [],
           "class_averages": {"10-A": 62.11}}  # fmt: skip
ANOMALIES = [
    {
        "rank": 1,
        "method": "duplicate_id",
        "student_id": "S00045",
        "severity": 2,
        "details": "two names",
    },
    {"rank": 2, "method": "term_jump", "student_id": "S00040", "severity": 46.9, "details": "rose"},
]


def make_api(reader: FakeReader | None = None, origin: str = "*") -> tuple[Api, FakeSigner]:
    reader = reader or FakeReader(
        [META],
        stats={"ds-1": StatsRecords(OVERALL, [])},
        anomalies={"ds-1": ANOMALIES},
    )
    signer = FakeSigner()
    api = Api(
        ListDatasets(reader),
        GetDataset(reader),
        GetStats(reader),
        GetAnomalies(reader),
        CreateUpload(signer, suffix=lambda: "1a2b3c4d"),
        allowed_origin=origin,
    )
    return api, signer


def event(
    method: str,
    resource: str,
    dataset_id: str | None = None,
    query: dict[str, str] | None = None,
    body: str | None = None,
    base64_body: bool = False,
) -> dict[str, Any]:
    """A REST API proxy event, shaped like the real one (None where API Gateway sends null)."""
    return {
        "httpMethod": method,
        "resource": resource,
        "path": resource.replace("{id}", dataset_id or ""),
        "pathParameters": {"id": dataset_id} if dataset_id else None,
        "queryStringParameters": query,
        "headers": {"x-api-key": "not-logged"},
        "body": body,
        "isBase64Encoded": base64_body,
    }


def call(api: Api, *args: Any, **kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
    response = api.handle(event(*args, **kwargs))
    return response["statusCode"], json.loads(response["body"]), response["headers"]


# --- the five routes -------------------------------------------------------------------


def test_list_datasets() -> None:
    status, body, _ = call(make_api()[0], "GET", "/datasets")
    assert status == 200
    assert [d["dataset_id"] for d in body["datasets"]] == ["ds-1"]
    assert body["next_cursor"] is None


def test_list_datasets_passes_limit_and_cursor_from_the_query_string() -> None:
    reader = FakeReader([META])
    call(make_api(reader)[0], "GET", "/datasets", query={"limit": "5", "cursor": "7"})
    assert reader.calls == [("list_datasets", 5, "7")]


def test_get_dataset() -> None:
    status, body, _ = call(make_api()[0], "GET", "/datasets/{id}", "ds-1")
    assert status == 200
    assert body["dataset_id"] == "ds-1"
    assert body["validation"]["total_rows"] == 904
    assert body["anomalies"] == {"count": 2, "stored": 2, "ml_skipped": None}


def test_get_stats() -> None:
    status, body, _ = call(make_api()[0], "GET", "/datasets/{id}/stats", "ds-1")
    assert status == 200
    assert body["overall"]["mean"] == 62.43 and body["student_count"] == 60


def test_get_anomalies_with_a_filter_and_a_limit() -> None:
    reader = FakeReader([META], anomalies={"ds-1": ANOMALIES})
    api = make_api(reader)[0]

    status, body, _ = call(
        api, "GET", "/datasets/{id}/anomalies", "ds-1", query={"method": "term_jump", "limit": "1"}
    )

    assert status == 200
    assert [item["student_id"] for item in body["items"]] == ["S00040"]
    assert reader.calls[-1] == ("list_anomalies", 1, None, "term_jump")


def test_create_upload_returns_201_with_a_signed_form() -> None:
    api, signer = make_api()

    status, body, _ = call(api, "POST", "/uploads", body=json.dumps({"filename": "results.csv"}))

    assert status == 201
    assert body["dataset_id"].startswith("ds-") and body["dataset_id"].endswith("-1a2b3c4d")
    assert body["upload"]["method"] == "POST" and body["upload"]["url"].startswith("https://")
    assert body["max_bytes"] == 50 * 1024 * 1024
    assert signer.requests[0][0] == f"uploads/{body['dataset_id']}/results.csv"


def test_a_base64_encoded_body_is_decoded() -> None:
    encoded = base64.b64encode(json.dumps({"filename": "results.csv"}).encode()).decode()
    status, _, _ = call(make_api()[0], "POST", "/uploads", body=encoded, base64_body=True)
    assert status == 201


# --- errors, always JSON with the same shape --------------------------------------------


def test_an_unknown_dataset_is_a_404_with_the_standard_error_body() -> None:
    status, body, _ = call(make_api()[0], "GET", "/datasets/{id}", "nope")
    assert status == 404
    assert body == {"error": {"code": "not_found", "message": "dataset 'nope' does not exist"}}


@pytest.mark.parametrize(
    ("resource", "query", "code"),
    [
        ("/datasets", {"limit": "0"}, "invalid_limit"),
        ("/datasets", {"limit": "abc"}, "invalid_limit"),
        ("/datasets/{id}/anomalies", {"method": "telepathy"}, "invalid_method"),
        ("/datasets/{id}/anomalies", {"limit": "501"}, "invalid_limit"),
    ],
)
def test_bad_query_parameters_are_a_400(resource: str, query: dict[str, str], code: str) -> None:
    status, body, _ = call(make_api()[0], "GET", resource, "ds-1", query=query)
    assert (status, body["error"]["code"]) == (400, code)


def test_a_malformed_dataset_id_is_a_400() -> None:
    status, body, _ = call(make_api()[0], "GET", "/datasets/{id}", "../etc")
    assert (status, body["error"]["code"]) == (400, "invalid_dataset_id")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (None, "invalid_body"),
        ("", "invalid_body"),
        ("not json", "invalid_json"),
        ("[1, 2]", "invalid_body"),
        ('"results.csv"', "invalid_body"),
        ("{}", "invalid_filename"),
        ('{"filename": 5}', "invalid_filename"),
        ('{"filename": "results.xlsx"}', "unsupported_file_type"),
        ('{"filename": "../../etc/passwd"}', "unsupported_file_type"),
    ],
)
def test_bad_upload_requests_are_a_400(body: str | None, code: str) -> None:
    api, signer = make_api()
    status, response, _ = call(api, "POST", "/uploads", body=body)
    assert (status, response["error"]["code"]) == (400, code)
    assert signer.requests == []  # nothing is signed for a bad request


def test_bad_base64_is_a_400() -> None:
    status, body, _ = call(make_api()[0], "POST", "/uploads", body="@@@", base64_body=True)
    assert (status, body["error"]["code"]) == (400, "invalid_body")


def test_an_oversized_body_is_a_413_and_is_not_parsed() -> None:
    huge = json.dumps({"filename": "a.csv", "padding": "x" * MAX_BODY_BYTES})
    status, body, _ = call(make_api()[0], "POST", "/uploads", body=huge)
    assert (status, body["error"]["code"]) == (413, "body_too_large")


def test_an_unknown_route_is_a_404() -> None:
    status, body, _ = call(make_api()[0], "GET", "/admin")
    assert (status, body["error"]["code"]) == (404, "route_not_found")


@pytest.mark.parametrize(
    ("method", "resource", "allowed"),
    [
        ("POST", "/datasets", "GET"),
        ("DELETE", "/datasets/{id}", "GET"),
        ("PUT", "/datasets/{id}/stats", "GET"),
        ("GET", "/uploads", "POST"),
    ],
)
def test_the_wrong_method_is_a_405_that_says_what_is_allowed(
    method: str, resource: str, allowed: str
) -> None:
    status, body, headers = call(make_api()[0], method, resource, "ds-1")
    assert (status, body["error"]["code"]) == (405, "method_not_allowed")
    assert headers["Allow"] == allowed


# --- unexpected failures never leak ------------------------------------------------------


class BrokenReader(FakeReader):
    def list_datasets(self, limit: int, cursor: str | None) -> Page:
        raise RuntimeError("password=hunter2 at db-host-17")


def test_an_unexpected_error_is_a_500_without_internal_details(logs) -> None:
    api = make_api(BrokenReader())[0]

    status, body, headers = call(api, "GET", "/datasets")

    assert status == 500
    assert body == {
        "error": {"code": "internal_error", "message": "something went wrong on our side"}
    }
    assert "hunter2" not in json.dumps(body)
    (error,) = [entry for entry in logs.entries if entry["level"] == "ERROR"]
    assert error["message"] == "unexpected error"
    assert (error["method"], error["resource"]) == ("GET", "/datasets")
    # the details go to the log, where only we can read them
    assert "hunter2" in error["exception"]
    assert headers["Access-Control-Allow-Origin"] == "*"  # even errors are readable by the browser


def test_a_response_that_cannot_be_valid_json_is_a_500_not_broken_json() -> None:
    bad = StatsRecords({**OVERALL, "mean": float("nan")}, [])
    api = make_api(FakeReader([META], stats={"ds-1": bad}))[0]

    status, body, _ = call(api, "GET", "/datasets/{id}/stats", "ds-1")

    assert (status, body["error"]["code"]) == (500, "internal_error")


# --- headers ---------------------------------------------------------------------------


def test_every_response_is_json_with_cors_and_no_caching() -> None:
    for resource, dataset_id in (("/datasets", None), ("/datasets/{id}", "nope")):
        _, _, headers = call(make_api()[0], "GET", resource, dataset_id)
        assert headers["Content-Type"] == "application/json"
        assert headers["Access-Control-Allow-Origin"] == "*"
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "Vary" not in headers


def test_a_specific_allowed_origin_is_sent_back_with_vary() -> None:
    _, _, headers = call(make_api(origin="https://dash.example.com")[0], "GET", "/datasets")
    assert headers["Access-Control-Allow-Origin"] == "https://dash.example.com"
    assert headers["Vary"] == "Origin"


def test_the_api_key_header_is_never_logged(logs) -> None:
    call(make_api()[0], "GET", "/datasets")
    assert "not-logged" not in logs.text
    (entry,) = logs.entries
    assert entry["message"] == "request handled"
    assert (entry["method"], entry["path"], entry["status"]) == ("GET", "/datasets", 200)


# --- wiring ----------------------------------------------------------------------------


def test_handler_builds_the_api_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("TABLE_NAME", "PipelineTable")
    monkeypatch.setenv("RAW_BUCKET", "raw-bucket")
    monkeypatch.setenv("ALLOWED_ORIGIN", "https://dash.example.com")
    api_handler._build_api.cache_clear()
    try:
        api = api_handler._build_api()
        assert api._allowed_origin == "https://dash.example.com"
        assert api is api_handler._build_api()  # cached per container
        assert api._create_upload._signer._bucket == "raw-bucket"
    finally:
        api_handler._build_api.cache_clear()


def test_the_handler_function_returns_a_response_for_a_bad_route(
    monkeypatch: pytest.MonkeyPatch, lambda_context
) -> None:
    """handler() end to end; an unknown route never reaches DynamoDB."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("TABLE_NAME", "PipelineTable")
    monkeypatch.setenv("RAW_BUCKET", "raw-bucket")
    api_handler._build_api.cache_clear()
    try:
        response = api_handler.handler(event("GET", "/nothing"), lambda_context)
        assert response["statusCode"] == 404
    finally:
        api_handler._build_api.cache_clear()


# --- the API Lambda has no pandas layer --------------------------------------------------

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.mark.parametrize(
    "module",
    [
        "gradeflow.entrypoints.api_handler",
        "gradeflow.infrastructure.dynamodb_reader",
        "gradeflow.infrastructure.s3_upload_signer",
    ],
)
def test_the_api_side_imports_no_heavy_libraries(module: str) -> None:
    code = (
        f"import {module}, sys, json; "
        "print(json.dumps(sorted(m for m in ('pandas', 'numpy', 'sklearn', 'pyarrow') "
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
