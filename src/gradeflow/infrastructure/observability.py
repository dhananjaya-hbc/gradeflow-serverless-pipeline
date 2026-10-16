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

from enum import StrEnum

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

# Decorator for every handler. It adds the Lambda request id, function name and a cold-start flag
# to each log line, and `clear_state=True` forgets keys added during the call (such as dataset_id)
# when it ends: without it a warm container would stamp the next dataset's logs with the old id.
with_logging = logger.inject_lambda_context(clear_state=True)


def record_metric(name: MetricName, value: float = 1) -> None:
    """Queue a count. It is published when the handler returns (`@metrics.log_metrics`)."""
    metrics.add_metric(name=name.value, unit=MetricUnit.Count, value=value)
