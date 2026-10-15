"""A small interpreter for the subset of Amazon States Language that pipeline.asl.json uses.

It lets the tests run the real state machine definition against the real handlers (with
in-memory fakes), so retries, catches, choices and ResultPath merging are checked without
deploying. Supported: Task (Lambda by name, SNS publish), Choice (BooleanEquals), Pass,
Succeed-by-End, Fail, Retry, Catch, ResultPath, Parameters with `.$` paths and States.Format.
Not supported (the definition does not use them): Map, Parallel, Wait, timeouts.
"""

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SNS_RESOURCE = "arn:aws:states:::sns:publish"
# Placeholders in the definition are replaced like the template's DefinitionSubstitutions.
SUBSTITUTIONS = {
    "ValidateFunctionArn": "fn:validate",
    "CleanFunctionArn": "fn:clean",
    "StatsFunctionArn": "fn:stats",
    "AnomaliesFunctionArn": "fn:anomalies",
    "StoreFunctionArn": "fn:store",
    "FailFunctionArn": "fn:fail",
    "AlertTopicArn": "topic:alerts",
}


@dataclass
class Execution:
    status: str  # SUCCEEDED or FAILED
    state: dict[str, Any]  # the final state
    visited: list[str] = field(default_factory=list)  # states entered, in order
    attempts: dict[str, int] = field(default_factory=dict)  # task invocations per state
    published: list[dict[str, Any]] = field(default_factory=list)  # SNS messages sent


class StatesRuntimeError(Exception):
    """Mirrors States.Runtime: a path that does not exist. Not catchable by Catch."""


class Emulator:
    def __init__(
        self,
        definition_text: str,
        lambdas: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
        sns_error: Exception | None = None,
    ) -> None:
        for name, value in SUBSTITUTIONS.items():
            definition_text = definition_text.replace("${" + name + "}", value)
        self._definition = json.loads(definition_text)
        self._lambdas = lambdas
        self._sns_error = sns_error
        self._check_supported()

    def _check_supported(self) -> None:
        """Fail at construction, not mid-run, if the definition uses something unsupported."""
        for name, spec in self._definition["States"].items():
            if spec["Type"] not in {"Task", "Choice", "Pass", "Fail"}:
                raise ValueError(f"state {name}: type {spec['Type']} is not supported")
            if spec["Type"] != "Task":
                continue
            resource = spec["Resource"]
            if resource != SNS_RESOURCE and resource not in self._lambdas:
                raise ValueError(f"state {name}: no Lambda provided for {resource}")
            path = spec.get("ResultPath", "$.x")
            if path is not None and not path.startswith("$."):
                raise ValueError(f"state {name}: ResultPath {path} is not supported")

    def run(self, state: dict[str, Any]) -> Execution:
        execution = Execution("RUNNING", copy.deepcopy(state))
        name = self._definition["StartAt"]
        while True:
            execution.visited.append(name)
            spec = self._definition["States"][name]
            kind = spec["Type"]
            if kind == "Fail":
                execution.status = "FAILED"
                return execution
            if kind == "Choice":
                name = self._choose(spec, execution.state)
                continue
            if kind == "Pass":
                self._store(execution, spec, spec["Result"])
                name = spec["Next"]
                continue
            next_state = self._run_task(name, spec, execution)
            if next_state is None:  # "End": true
                execution.status = "SUCCEEDED"
                return execution
            name = next_state

    # --- state types ---

    def _choose(self, spec: dict[str, Any], state: dict[str, Any]) -> str:
        for rule in spec["Choices"]:
            if _get(state, rule["Variable"]) == rule["BooleanEquals"]:
                return rule["Next"]
        return spec["Default"]

    def _run_task(self, name: str, spec: dict[str, Any], execution: Execution) -> str | None:
        retries_done = 0
        while True:
            execution.attempts[name] = execution.attempts.get(name, 0) + 1
            try:
                result = self._invoke(spec, execution)
            except StatesRuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001 - a Lambda failure of any kind
                error = type(exc).__name__
                retry = _matching(spec.get("Retry", []), error)
                if retry and retries_done < retry["MaxAttempts"]:
                    retries_done += 1
                    continue
                catch = _matching(spec.get("Catch", []), error)
                if catch is None:
                    raise
                self._store(execution, catch, {"Error": error, "Cause": str(exc)})
                return catch["Next"]
            self._store(execution, spec, result)
            return spec.get("Next")

    def _invoke(self, spec: dict[str, Any], execution: Execution) -> dict[str, Any]:
        resource = spec["Resource"]
        if resource == SNS_RESOURCE:
            if self._sns_error is not None:
                raise self._sns_error
            params = _resolve(spec["Parameters"], execution.state)
            execution.published.append(params)
            return {}
        event = json.loads(json.dumps(execution.state))  # what the Lambda receives
        return json.loads(json.dumps(self._lambdas[resource](event)))  # what it returns

    @staticmethod
    def _store(execution: Execution, spec: dict[str, Any], result: Any) -> None:
        if "ResultPath" not in spec:  # default: the result replaces the whole state
            execution.state = result
            return
        path = spec["ResultPath"]
        if path is None:  # discard the result
            return
        execution.state[path[2:]] = result


# --- helpers ---


def _matching(rules: list[dict[str, Any]], error: str) -> dict[str, Any] | None:
    """First Retry/Catch entry that matches. Lambda failures match States.ALL/TaskFailed."""
    for rule in rules:
        if {error, "States.ALL", "States.TaskFailed"} & set(rule["ErrorEquals"]):
            return rule
    return None


def _get(state: dict[str, Any], path: str) -> Any:
    value: Any = state
    for part in path.removeprefix("$.").split("."):
        if not isinstance(value, dict) or part not in value:
            raise StatesRuntimeError(f"path {path} does not exist in the state")
        value = value[part]
    return value


def _resolve(params: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Resolve Parameters entries ending in `.$` (a path or States.Format)."""
    resolved: dict[str, Any] = {}
    for key, value in params.items():
        if not key.endswith(".$"):
            resolved[key] = value
        elif value.startswith("States.Format("):
            template, args = re.fullmatch(r"States\.Format\('(.*)',\s*(.*)\)", value).groups()
            values = [str(_get(state, arg.strip())) for arg in args.split(",")]
            for item in values:
                template = template.replace("{}", item, 1)
            resolved[key[:-2]] = template
        else:
            resolved[key[:-2]] = _get(state, value)
    return resolved
