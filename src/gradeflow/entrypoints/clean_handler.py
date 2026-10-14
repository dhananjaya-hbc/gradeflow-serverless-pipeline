"""Lambda handler for the Clean step. Input: the pipeline state. Output: `cleaning`.

Handler path in template.yaml: gradeflow.entrypoints.clean_handler.handler
"""

import logging
from functools import cache
from typing import Any

from gradeflow.application.pipeline_state import PipelineState
from gradeflow.application.pipeline_steps import CleanStep
from gradeflow.infrastructure.config import load_clean_config
from gradeflow.infrastructure.s3_artifacts import S3DatasetArtifacts

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return process(event, _build_step())


@cache
def _build_step() -> CleanStep:
    return CleanStep(artifacts=S3DatasetArtifacts(load_clean_config().processed_bucket))


def process(event: dict[str, Any], step: CleanStep) -> dict[str, Any]:
    state = PipelineState.from_dict(event)
    outcome = step.execute(state)
    logger.info(
        "cleaned dataset %s: clean=%d duplicates_removed=%d",
        state.dataset_id,
        outcome.clean_rows,
        outcome.duplicates_removed,
    )
    return outcome.to_dict()
