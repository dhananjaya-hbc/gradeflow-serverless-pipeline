"""Lambda handler for the Store step. Input: the pipeline state. Output: the final status.

Handler path in template.yaml: gradeflow.entrypoints.store_handler.handler
"""

from functools import cache
from typing import Any

from gradeflow.application.pipeline_state import PipelineState
from gradeflow.application.pipeline_steps import StoreStep
from gradeflow.infrastructure.config import load_store_config
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
from gradeflow.infrastructure.observability import logger, with_logging
from gradeflow.infrastructure.s3_artifacts import S3DatasetArtifacts


@with_logging
def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return process(event, _build_step())


@cache
def _build_step() -> StoreStep:
    config = load_store_config()
    return StoreStep(
        repository=DynamoDbDatasetRepository(config.table_name),
        artifacts=S3DatasetArtifacts(config.processed_bucket),
        max_anomalies=config.max_anomalies,
    )


def process(event: dict[str, Any], step: StoreStep) -> dict[str, Any]:
    meta = step.execute(PipelineState.from_dict(event))
    logger.info(
        "dataset stored",
        dataset_id=meta.dataset_id,
        status=meta.status.value,
        anomaly_count=meta.anomaly_count,
    )
    return {"dataset_id": meta.dataset_id, "status": meta.status.value}
