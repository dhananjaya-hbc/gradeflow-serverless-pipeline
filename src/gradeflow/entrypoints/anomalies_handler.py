"""Lambda handler for the Anomalies step. Input: the pipeline state. Output: `anomalies`.

This one runs from a container image (scikit-learn is too big for a zip Lambda), built from
docker/anomalies/Dockerfile. The image's command is the path below.

Handler path in template.yaml: gradeflow.entrypoints.anomalies_handler.handler
"""

import logging
from functools import cache
from typing import Any

from gradeflow.application.anomaly_step import DetectAnomaliesStep
from gradeflow.application.pipeline_state import PipelineState
from gradeflow.infrastructure.config import load_anomalies_config
from gradeflow.infrastructure.s3_artifacts import S3DatasetArtifacts

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return process(event, _build_step())


@cache
def _build_step() -> DetectAnomaliesStep:
    config = load_anomalies_config()
    return DetectAnomaliesStep(
        artifacts=S3DatasetArtifacts(config.processed_bucket),
        zscore_limit=config.zscore_limit,
        jump_threshold=config.jump_threshold,
        contamination=config.contamination,
    )


def process(event: dict[str, Any], step: DetectAnomaliesStep) -> dict[str, Any]:
    state = PipelineState.from_dict(event)
    outcome = step.execute(state)
    logger.info(
        "anomalies for dataset %s: total=%d by_method=%s ml_skipped=%s",
        state.dataset_id,
        outcome.total,
        outcome.by_method,
        outcome.ml_skipped,
    )
    return outcome.to_dict()
