"""Lambda handler for the Validate step. Input: the pipeline state. Output: `validation`.

Handler path in template.yaml: gradeflow.entrypoints.validate_handler.handler
"""

from functools import cache
from typing import Any

from gradeflow.application.pipeline_state import PipelineState
from gradeflow.application.pipeline_steps import ValidateStep
from gradeflow.infrastructure.config import load_validate_config
from gradeflow.infrastructure.observability import logger, with_logging
from gradeflow.infrastructure.s3_artifacts import S3DatasetArtifacts
from gradeflow.infrastructure.s3_storage import S3FileStorage


@with_logging
def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Lambda entry point."""
    return process(event, _build_step())


@cache
def _build_step() -> ValidateStep:
    config = load_validate_config()
    return ValidateStep(
        storage=S3FileStorage(),
        artifacts=S3DatasetArtifacts(config.processed_bucket),
        min_valid_ratio=config.min_valid_ratio,
    )


def process(event: dict[str, Any], step: ValidateStep) -> dict[str, Any]:
    state = PipelineState.from_dict(event)
    outcome = step.execute(state)
    # An invalid file is a normal outcome (the Fail step handles it), but worth a WARNING.
    log = logger.info if outcome.valid else logger.warning
    log(
        "validation finished",
        dataset_id=state.dataset_id,
        valid=outcome.valid,
        total_rows=outcome.total_rows,
        valid_rows=outcome.valid_rows,
        invalid_rows=outcome.invalid_rows,
        error_message=outcome.error_message,
    )
    return outcome.to_dict()
