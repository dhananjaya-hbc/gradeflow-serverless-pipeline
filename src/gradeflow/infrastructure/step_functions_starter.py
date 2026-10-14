"""Step Functions adapter for the WorkflowStarter port."""

import json
from typing import Any

import boto3
from botocore.exceptions import ClientError


class StepFunctionsStarter:
    """Starts executions of the pipeline state machine."""

    def __init__(self, state_machine_arn: str, client: Any = None) -> None:
        self._arn = state_machine_arn
        self._client = client or boto3.client("stepfunctions")

    def start(self, name: str, payload: dict[str, Any]) -> bool:
        """Start an execution. False if one with this name already exists (a redelivery).

        Any other error is raised, so SQS retries the message instead of losing the upload.
        """
        try:
            self._client.start_execution(
                stateMachineArn=self._arn, name=name, input=json.dumps(payload)
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ExecutionAlreadyExists":
                return False
            raise
        return True
