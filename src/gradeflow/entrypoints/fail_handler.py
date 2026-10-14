"""Lambda handler for the failure branch. Input: the pipeline state (with `validation`
saying the file is bad, or `error` set by the state machine's Catch).

Handler path in template.yaml: gradeflow.entrypoints.fail_handler.handler
"""

import logging
from functools import cache
from typing import Any

from gradeflow.application.pipeline_state import PipelineState
from gradeflow.application.pipeline_steps import RejectDataset
from gradeflow.infrastructure.config import load_reject_config
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository
from gradeflow.infrastructure.s3_quarantine import S3Quarantine

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return process(event, _build_step())


@cache
def _build_step() -> RejectDataset:
    config = load_reject_config()
    return RejectDataset(
        quarantine=S3Quarantine(config.quarantine_bucket),
        repository=DynamoDbDatasetRepository(config.table_name),
    )


def process(event: dict[str, Any], step: RejectDataset) -> dict[str, Any]:
    meta = step.execute(PipelineState.from_dict(event))
    logger.warning("dataset %s FAILED: %s", meta.dataset_id, meta.error_message)
    return {
        "dataset_id": meta.dataset_id,
        "status": meta.status.value,
        "error_message": meta.error_message,
    }
