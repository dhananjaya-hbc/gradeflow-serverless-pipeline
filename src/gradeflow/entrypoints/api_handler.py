"""Lambda handler for the Analytics API (API Gateway REST API, Lambda proxy integration).

Handler path in template.yaml: gradeflow.entrypoints.api_handler.handler

This is only the HTTP layer: pick the route, read the parameters, call a use case, and turn
the result (or a QueryError) into a JSON response. The logic lives in the use cases.

Routes (API Gateway has already checked the API key before this runs):
    GET  /datasets                    GET  /datasets/{id}
    GET  /datasets/{id}/stats         GET  /datasets/{id}/anomalies
    POST /uploads
"""

import base64
import binascii
import json
import logging
from functools import cache
from typing import Any

from gradeflow.application.queries import (
    BadRequest,
    CreateUpload,
    GetAnomalies,
    GetDataset,
    GetStats,
    ListDatasets,
    QueryError,
)
from gradeflow.infrastructure.config import load_api_config
from gradeflow.infrastructure.dynamodb_reader import DynamoDbDatasetReader
from gradeflow.infrastructure.s3_upload_signer import S3UploadSigner

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MAX_BODY_BYTES = 4096  # the only body we read is {"filename": "..."}


class Api:
    """Routes API Gateway proxy events to the use cases."""

    def __init__(
        self,
        list_datasets: ListDatasets,
        get_dataset: GetDataset,
        get_stats: GetStats,
        get_anomalies: GetAnomalies,
        create_upload: CreateUpload,
        allowed_origin: str = "*",
    ) -> None:
        self._list_datasets = list_datasets
        self._get_dataset = get_dataset
        self._get_stats = get_stats
        self._get_anomalies = get_anomalies
        self._create_upload = create_upload
        self._allowed_origin = allowed_origin
        # route template (API Gateway's `resource`) -> method -> function(event) -> (status, body)
        self._routes = {
            "/datasets": {"GET": self._on_list},
            "/datasets/{id}": {"GET": self._on_dataset},
            "/datasets/{id}/stats": {"GET": self._on_stats},
            "/datasets/{id}/anomalies": {"GET": self._on_anomalies},
            "/uploads": {"POST": self._on_upload},
        }

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        """Always returns a complete HTTP response: errors become JSON, never an exception."""
        method, resource = event.get("httpMethod", ""), event.get("resource", "")
        extra_headers: dict[str, str] = {}
        try:
            status, body = self._dispatch(method, resource, event)
        except _MethodNotAllowed as exc:
            status, extra_headers = 405, {"Allow": ", ".join(exc.allowed)}
            body = _error("method_not_allowed", f"{method} is not allowed here")
        except QueryError as exc:
            status, body = exc.status, _error(exc.code, exc.message)
        except Exception:
            logger.exception("unexpected error on %s %s", method, resource)
            status, body = 500, _error("internal_error", "something went wrong on our side")
        try:
            text = json.dumps(body, allow_nan=False)  # NaN is not valid JSON: refuse, not emit
        except ValueError:
            logger.exception("response for %s %s is not valid JSON", method, resource)
            status, text = 500, json.dumps(_error("internal_error", "something went wrong"))
        logger.info("%s %s -> %d", method, event.get("path", resource), status)
        return {"statusCode": status, "headers": self._headers(extra_headers), "body": text}

    # --- routing ---------------------------------------------------------------------

    def _dispatch(self, method: str, resource: str, event: dict[str, Any]) -> tuple[int, Any]:
        methods = self._routes.get(resource)
        if methods is None:
            return 404, _error("route_not_found", f"there is no route {resource or '(none)'}")
        function = methods.get(method)
        if function is None:
            raise _MethodNotAllowed(sorted(methods))
        return function(event)

    def _on_list(self, event: dict[str, Any]) -> tuple[int, Any]:
        query = _query(event)
        return 200, self._list_datasets.execute(query.get("limit"), query.get("cursor"))

    def _on_dataset(self, event: dict[str, Any]) -> tuple[int, Any]:
        return 200, self._get_dataset.execute(_dataset_id(event))

    def _on_stats(self, event: dict[str, Any]) -> tuple[int, Any]:
        return 200, self._get_stats.execute(_dataset_id(event))

    def _on_anomalies(self, event: dict[str, Any]) -> tuple[int, Any]:
        query = _query(event)
        result = self._get_anomalies.execute(
            _dataset_id(event), query.get("method"), query.get("limit"), query.get("cursor")
        )
        return 200, result

    def _on_upload(self, event: dict[str, Any]) -> tuple[int, Any]:
        return 201, self._create_upload.execute(_json_body(event).get("filename"))

    # --- responses -------------------------------------------------------------------

    def _headers(self, extra: dict[str, str]) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": self._allowed_origin,
            "Cache-Control": "no-store",  # results change as datasets are processed
            "X-Content-Type-Options": "nosniff",
        }
        if self._allowed_origin != "*":
            headers["Vary"] = "Origin"
        return {**headers, **extra}


class _MethodNotAllowed(Exception):
    def __init__(self, allowed: list[str]) -> None:
        super().__init__("method not allowed")
        self.allowed = allowed


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _query(event: dict[str, Any]) -> dict[str, str]:
    return event.get("queryStringParameters") or {}


def _dataset_id(event: dict[str, Any]) -> str:
    return (event.get("pathParameters") or {}).get("id", "")


def _json_body(event: dict[str, Any]) -> dict[str, Any]:
    """The request body as a JSON object, or a 400/413 explaining what is wrong."""
    raw = event.get("body")
    if raw is None or raw == "":
        raise BadRequest('send a JSON body such as {"filename": "results.csv"}', "invalid_body")
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise BadRequest("the body is not valid base64 text", "invalid_body") from exc
    if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        error = BadRequest(f"the body may be at most {MAX_BODY_BYTES} bytes", "body_too_large")
        error.status = 413
        raise error
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BadRequest("the body is not valid JSON", "invalid_json") from exc
    if not isinstance(body, dict):
        raise BadRequest("the body must be a JSON object", "invalid_body")
    return body


# --- Lambda wiring -------------------------------------------------------------------


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return _build_api().handle(event)


@cache
def _build_api() -> Api:
    """Build adapters once per Lambda container; warm invocations reuse them."""
    config = load_api_config()
    reader = DynamoDbDatasetReader(config.table_name)
    return Api(
        list_datasets=ListDatasets(reader),
        get_dataset=GetDataset(reader),
        get_stats=GetStats(reader),
        get_anomalies=GetAnomalies(reader),
        create_upload=CreateUpload(S3UploadSigner(config.raw_bucket)),
        allowed_origin=config.allowed_origin,
    )
