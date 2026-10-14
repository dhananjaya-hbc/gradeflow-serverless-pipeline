"""Checks that template.yaml, the handlers, the config loaders and the state machine agree.

These are mistakes that normally only show up after a deploy: a wrong handler path, an env
var the code needs but the template forgot, a placeholder with no matching function, or a
permission that drifts from the design.
"""

import importlib
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from gradeflow.infrastructure import config

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_TEXT = (ROOT / "template.yaml").read_text()
ASL_TEXT = (ROOT / "statemachine" / "pipeline.asl.json").read_text()


class _TemplateLoader(yaml.SafeLoader):
    """Reads CloudFormation YAML: intrinsic tags like !Ref become {"Ref": ...} dicts."""


def _intrinsic(loader: yaml.Loader, suffix: str, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_TemplateLoader.add_multi_constructor("!", _intrinsic)
RESOURCES: dict[str, dict[str, Any]] = yaml.load(TEMPLATE_TEXT, Loader=_TemplateLoader)["Resources"]
FUNCTIONS = {
    name: resource["Properties"]
    for name, resource in RESOURCES.items()
    if resource["Type"] == "AWS::Serverless::Function"
}

# which config loader each function's handler uses at start-up
LOADERS = {
    "TriggerFunction": config.load_trigger_config,
    "ValidateFunction": config.load_validate_config,
    "CleanFunction": config.load_clean_config,
    "StatsFunction": config.load_stats_config,
    "StoreFunction": config.load_store_config,
    "FailFunction": config.load_reject_config,
}


def test_every_function_has_a_config_loader_in_this_test() -> None:
    """Adding a function without registering it above would skip the checks below."""
    assert set(FUNCTIONS) == set(LOADERS)


@pytest.mark.parametrize("name", sorted(FUNCTIONS))
def test_handler_path_points_at_a_real_handler(name: str) -> None:
    handler_path = FUNCTIONS[name]["Handler"]
    module_name, _, function_name = handler_path.rpartition(".")
    module = importlib.import_module(module_name)
    assert callable(getattr(module, function_name)), f"{handler_path} is not a function"


@pytest.mark.parametrize("name", sorted(FUNCTIONS))
def test_template_provides_every_env_var_the_handler_needs(name: str) -> None:
    """The loader reads exactly what this function is configured with (or raises)."""
    names = set(FUNCTIONS[name].get("Environment", {}).get("Variables", {}))
    dummy = {"MIN_VALID_RATIO": "0.8", "PASS_MARK": "50"}
    env = {variable: dummy.get(variable, "x") for variable in names}

    LOADERS[name](env)  # raises ConfigError if the template forgot a variable


@pytest.mark.parametrize("name", sorted(FUNCTIONS))
def test_function_has_a_log_group_with_retention(name: str) -> None:
    log_group = RESOURCES[f"{name}LogGroup"]["Properties"]
    assert log_group["RetentionInDays"] <= 30


def test_every_asl_placeholder_is_substituted_by_a_real_function_or_the_topic() -> None:
    placeholders = set(re.findall(r"\$\{(\w+)\}", ASL_TEXT))
    machine = RESOURCES["PipelineStateMachine"]["Properties"]
    substitutions = set(machine["DefinitionSubstitutions"])

    assert placeholders == substitutions
    for placeholder in placeholders - {"AlertTopicArn"}:
        assert placeholder.removesuffix("Arn") in FUNCTIONS


def test_the_state_machine_may_invoke_exactly_the_workflow_functions() -> None:
    policies = RESOURCES["PipelineStateMachine"]["Properties"]["Policies"]
    invoked = {
        policy["LambdaInvokePolicy"]["FunctionName"]["Ref"]
        for policy in policies
        if "LambdaInvokePolicy" in policy
    }
    assert invoked == set(FUNCTIONS) - {"TriggerFunction"}  # the trigger calls the machine


# --- the intended permissions, pinned so changes are deliberate -----------------------


def policy_kinds(name: str) -> set[str]:
    return {next(iter(policy)) for policy in FUNCTIONS[name]["Policies"]}


def dynamodb_actions(name: str) -> set[str]:
    actions: set[str] = set()
    for policy in FUNCTIONS[name]["Policies"]:
        for statement in policy.get("Statement", []):
            actions.update(a for a in statement["Action"] if a.startswith("dynamodb:"))
    return actions


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("TriggerFunction", {"S3ReadPolicy", "Statement", "StepFunctionsExecutionPolicy"}),
        ("ValidateFunction", {"S3ReadPolicy", "S3WritePolicy"}),  # no DynamoDB
        ("CleanFunction", {"S3ReadPolicy", "S3WritePolicy"}),  # no DynamoDB
        ("StatsFunction", {"S3ReadPolicy"}),  # read-only
        ("StoreFunction", {"Statement"}),  # DynamoDB only, no S3
        ("FailFunction", {"S3ReadPolicy", "S3WritePolicy", "Statement"}),
    ],
)
def test_each_function_gets_only_the_kinds_of_access_its_step_needs(
    name: str, expected: set[str]
) -> None:
    assert policy_kinds(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("TriggerFunction", {"dynamodb:PutItem", "dynamodb:GetItem"}),
        (
            "StoreFunction",
            {
                "dynamodb:PutItem",
                "dynamodb:Query",
                "dynamodb:DeleteItem",
                "dynamodb:BatchWriteItem",
            },
        ),
        ("FailFunction", {"dynamodb:PutItem"}),
        ("ValidateFunction", set()),
        ("CleanFunction", set()),
        ("StatsFunction", set()),
    ],
)
def test_dynamodb_actions_are_exactly_the_ones_the_step_uses(name: str, expected: set[str]) -> None:
    assert dynamodb_actions(name) == expected


def test_only_the_trigger_reads_the_queue() -> None:
    sqs_functions = {
        name
        for name, props in FUNCTIONS.items()
        if any(event["Type"] == "SQS" for event in props.get("Events", {}).values())
    }
    assert sqs_functions == {"TriggerFunction"}
    assert FUNCTIONS["TriggerFunction"]["Events"]["UploadedFile"]["Properties"]["BatchSize"] == 1


def test_queue_visibility_timeout_is_at_least_six_times_the_trigger_timeout() -> None:
    visibility = RESOURCES["UploadQueue"]["Properties"]["VisibilityTimeout"]
    assert visibility >= 6 * FUNCTIONS["TriggerFunction"]["Timeout"]
