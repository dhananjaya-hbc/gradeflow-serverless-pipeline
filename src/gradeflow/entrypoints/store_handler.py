"""Lambda handler for the Store step. Input: the pipeline state. Output: the final status.

Handler path in template.yaml: gradeflow.entrypoints.store_handler.handler
"""

import logging
from functools import cache
from typing import Any

from gradeflow.application.pipeline_state import PipelineState
from gradeflow.application.pipeline_steps import StoreStep
from gradeflow.infrastructure.config import load_store_config
from gradeflow.infrastructure.dynamodb_repository import DynamoDbDatasetRepository

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return process(event, _build_step())


@cache
def _build_step() -> StoreStep:
    return StoreStep(repository=DynamoDbDatasetRepository(load_store_config().table_name))


def process(event: dict[str, Any], step: StoreStep) -> dict[str, Any]:
    meta = step.execute(PipelineState.from_dict(event))
    logger.info("stored dataset %s: status=%s", meta.dataset_id, meta.status)
    return {"dataset_id": meta.dataset_id, "status": meta.status.value}
