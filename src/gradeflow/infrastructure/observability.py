"""Logging and metrics for the Lambda handlers, built on Powertools for AWS Lambda.

- Logs are one JSON object per line, so CloudWatch Logs Insights can filter on any field, for
  example `filter dataset_id = "ds-20261016-b537f020"` follows one dataset through every step.
- Metrics use the embedded metric format: a metric is a specially shaped log line that CloudWatch
  turns into a metric. No `PutMetricData` permission is needed.

Lives in `infrastructure/`: the domain and application layers never import it (the architecture
test enforces that). Handlers read the outcome of a use case and report it from here.

Every metric costs money beyond the first 10 custom metrics in an account, and a different
dimension value counts as a new metric. So the names are a closed list (`MetricName`), they all
share the single `service` dimension, and a test keeps the list at 10 or fewer.
"""

import functools
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit

SERVICE_NAME = "gradeflow"
METRICS_NAMESPACE = "GradeFlow"


class MetricName(StrEnum):
    """Every custom metric the pipeline publishes (the CloudWatch dashboard reads these names)."""

    ROWS_RECEIVED = "RowsReceived"  # rows in an uploaded file, counted by Validate
    ROWS_REJECTED = "RowsRejected"  # rows that failed validation
    ANOMALIES_FOUND = "AnomaliesFound"  # findings of a completed dataset
    DATASETS_COMPLETED = "DatasetsCompleted"
    DATASETS_FAILED = "DatasetsFailed"
    DUPLICATES_SKIPPED = "DuplicatesSkipped"  # uploads identical to an earlier file
    API_SERVER_ERRORS = "ApiServerErrors"  # API responses with a 5xx status


# The level comes from the POWERTOOLS_LOG_LEVEL environment variable (INFO when it is not set).
logger = Logger(service=SERVICE_NAME)
metrics = Metrics(namespace=METRICS_NAMESPACE, service=SERVICE_NAME)

_inject_context = logger.inject_lambda_context(clear_state=True)


def with_logging(handler: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
    """Decorator for every Lambda handler.

    - Each log line gets the Lambda request id, function name and a cold-start flag.
    - `clear_state=True` forgets the keys of the previous call when a new call starts: without it
      a warm container would stamp the next dataset's log lines with the old dataset_id.
    - A workflow step receives the pipeline state, so its `dataset_id` is added to every line.
    - An exception that escapes the handler is logged as one JSON line (with the traceback and the
      dataset_id) and raised again, so Step Functions and SQS still see the failure and retry.
      Lambda's own traceback output is plain text that Logs Insights cannot filter by field.
    """

    @functools.wraps(handler)
    def wrapper(event: Any, context: Any) -> Any:
        if isinstance(event, dict) and isinstance(event.get("dataset_id"), str):
            logger.append_keys(dataset_id=event["dataset_id"])
        try:
            return handler(event, context)
        except Exception:
            logger.exception("handler failed")
            raise

    return _inject_context(wrapper)


def record_metric(name: MetricName, value: float = 1) -> None:
    """Queue a count. It is published when the handler returns (`@metrics.log_metrics`)."""
    metrics.add_metric(name=name.value, unit=MetricUnit.Count, value=value)
