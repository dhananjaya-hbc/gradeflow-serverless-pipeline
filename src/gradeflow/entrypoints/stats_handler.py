"""Lambda handler for the Stats step. Input: the pipeline state. Output: `stats` (JSON).

Handler path in template.yaml: gradeflow.entrypoints.stats_handler.handler
"""

import logging
from functools import cache
from typing import Any

from gradeflow.application.pipeline_state import PipelineState
from gradeflow.application.pipeline_steps import StatsStep
from gradeflow.infrastructure.config import load_stats_config
from gradeflow.infrastructure.s3_artifacts import S3DatasetArtifacts

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return process(event, _build_step())


@cache
def _build_step() -> StatsStep:
    config = load_stats_config()
    return StatsStep(
        artifacts=S3DatasetArtifacts(config.processed_bucket), pass_mark=config.pass_mark
    )


def process(event: dict[str, Any], step: StatsStep) -> dict[str, Any]:
    state = PipelineState.from_dict(event)
    stats = step.execute(state)
    logger.info(
        "computed stats for dataset %s: %d groups", state.dataset_id, len(stats["by_subject_term"])
    )
    return stats
