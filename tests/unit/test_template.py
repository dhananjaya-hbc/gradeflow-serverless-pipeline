"""Checks that template.yaml, the handlers, the config loaders and the state machine agree.

These are mistakes that normally only show up after a deploy: a wrong handler path, an env
var the code needs but the template forgot, a placeholder with no matching function, or a
permission that drifts from the design.
"""

import importlib
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from gradeflow.domain import anomalies as anomaly_defaults
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
TEMPLATE: dict[str, Any] = yaml.load(TEMPLATE_TEXT, Loader=_TemplateLoader)
RESOURCES: dict[str, dict[str, Any]] = TEMPLATE["Resources"]
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
    "AnomaliesFunction": config.load_anomalies_config,
    "ApiFunction": config.load_api_config,
    "StoreFunction": config.load_store_config,
    "FailFunction": config.load_reject_config,
}


def test_every_function_has_a_config_loader_in_this_test() -> None:
    """Adding a function without registering it above would skip the checks below."""
    assert set(FUNCTIONS) == set(LOADERS)


def handler_path(name: str) -> str:
    """Zip functions name their handler; image functions name it in ImageConfig.Command."""
    properties = FUNCTIONS[name]
    return (
        properties["Handler"]
        if "Handler" in properties
        else properties["ImageConfig"]["Command"][0]
    )


@pytest.mark.parametrize("name", sorted(FUNCTIONS))
def test_handler_path_points_at_a_real_handler(name: str) -> None:
    module_name, _, function_name = handler_path(name).rpartition(".")
    module = importlib.import_module(module_name)
    assert callable(getattr(module, function_name)), f"{handler_path(name)} is not a function"


@pytest.mark.parametrize("name", sorted(FUNCTIONS))
def test_template_provides_every_env_var_the_handler_needs(name: str) -> None:
    """The loader reads exactly what this function is configured with (or raises)."""
    names = set(FUNCTIONS[name].get("Environment", {}).get("Variables", {}))
    dummy = {  # numeric settings need a valid number, the rest just need to be non-empty
        "MIN_VALID_RATIO": "0.8",
        "PASS_MARK": "50",
        "ZSCORE_LIMIT": "3",
        "JUMP_THRESHOLD": "30",
        "IF_CONTAMINATION": "0.05",
        "MAX_ANOMALIES": "500",
    }
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
    assert invoked == set(FUNCTIONS) - {"TriggerFunction", "ApiFunction"}  # those two are not steps


# --- the intended permissions, pinned so changes are deliberate -----------------------


def policy_kinds(name: str) -> set[str]:
    return {next(iter(policy)) for policy in FUNCTIONS[name]["Policies"]}


def dynamodb_actions(name: str) -> set[str]:
    actions: set[str] = set()
    for policy in FUNCTIONS[name]["Policies"]:
        for statement in policy.get("Statement", []):
            action = statement["Action"]
            actions.update(
                a
                for a in ([action] if isinstance(action, str) else action)
                if a.startswith("dynamodb:")
            )
    return actions


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("TriggerFunction", {"S3ReadPolicy", "Statement", "StepFunctionsExecutionPolicy"}),
        ("ValidateFunction", {"S3ReadPolicy", "S3WritePolicy"}),  # no DynamoDB
        ("CleanFunction", {"S3ReadPolicy", "S3WritePolicy"}),  # no DynamoDB
        ("StatsFunction", {"S3ReadPolicy"}),  # read-only
        ("AnomaliesFunction", {"S3ReadPolicy", "S3WritePolicy"}),  # no DynamoDB
        ("ApiFunction", {"Statement"}),  # read DynamoDB, sign S3 uploads: both custom statements
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
        ("AnomaliesFunction", set()),
        ("ApiFunction", {"dynamodb:GetItem", "dynamodb:Query"}),  # read-only
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


# --- the container-image function, its settings and the wiring around it --------------

IMAGE_FUNCTIONS = {
    name
    for name, props in RESOURCES.items()
    if props.get("Properties", {}).get("PackageType") == "Image"
}


def test_only_the_anomalies_function_is_a_container_image() -> None:
    assert {"AnomaliesFunction"} == IMAGE_FUNCTIONS


def test_image_function_points_at_a_dockerfile_that_runs_the_same_handler() -> None:
    metadata = RESOURCES["AnomaliesFunction"]["Metadata"]
    assert metadata["DockerContext"] == "."  # the repository root: the Dockerfile copies src/
    dockerfile = ROOT / metadata["Dockerfile"]
    assert dockerfile.is_file()

    text = dockerfile.read_text()
    command = FUNCTIONS["AnomaliesFunction"]["ImageConfig"]["Command"][0]
    assert f'CMD ["{command}"]' in text  # the template and the image agree
    for copied in re.findall(r"^COPY (\S+) ", text, flags=re.MULTILINE):
        assert (ROOT / copied).exists(), f"the Dockerfile copies {copied}, which does not exist"


def test_image_function_has_none_of_the_zip_only_settings() -> None:
    properties = FUNCTIONS["AnomaliesFunction"]
    assert not {"Runtime", "CodeUri", "Layers", "Handler"} & set(properties)


def test_the_six_pipeline_functions_each_set_runtime_code_and_the_pandas_layer() -> None:
    pipeline_functions = set(FUNCTIONS) - IMAGE_FUNCTIONS - {"ApiFunction"}
    assert len(pipeline_functions) == 6
    for name in pipeline_functions:
        properties = FUNCTIONS[name]
        assert properties["Runtime"] == "python3.12", name
        assert properties["CodeUri"] == "src/", name
        assert properties["Layers"] == [{"Ref": "PandasLayerArn"}], name


def test_the_api_function_is_a_plain_zip_function_with_no_pandas_layer() -> None:
    """It only reads stored items, so it skips the heavy layer and starts faster."""
    properties = FUNCTIONS["ApiFunction"]
    assert (properties["Runtime"], properties["CodeUri"]) == ("python3.12", "src/")
    assert "Layers" not in properties and "PackageType" not in properties


def test_globals_hold_only_settings_valid_for_both_kinds_of_function() -> None:
    assert not {"Runtime", "CodeUri", "Layers", "Handler"} & set(TEMPLATE["Globals"]["Function"])


def test_store_can_read_only_the_work_prefix_of_the_processed_bucket() -> None:
    statements = [
        statement
        for policy in FUNCTIONS["StoreFunction"]["Policies"]
        for statement in policy.get("Statement", [])
        if statement["Action"] == "s3:GetObject"
    ]
    assert len(statements) == 1
    assert statements[0]["Resource"]["Sub"] == "${ProcessedBucket.Arn}/work/*"


def test_anomaly_settings_are_wired_from_parameters_to_environment_variables() -> None:
    anomalies_env = FUNCTIONS["AnomaliesFunction"]["Environment"]["Variables"]
    assert anomalies_env["ZSCORE_LIMIT"] == {"Ref": "ZScoreLimit"}
    assert anomalies_env["JUMP_THRESHOLD"] == {"Ref": "JumpThreshold"}
    assert anomalies_env["IF_CONTAMINATION"] == {"Ref": "IfContamination"}
    assert FUNCTIONS["StoreFunction"]["Environment"]["Variables"]["MAX_ANOMALIES"] == {
        "Ref": "MaxAnomalies"
    }


def test_template_defaults_match_the_defaults_in_the_code() -> None:
    """One source of truth per number: the template, the config loader and the detectors."""
    parameters = TEMPLATE["Parameters"]
    assert parameters["ZScoreLimit"]["Default"] == config.DEFAULT_ZSCORE_LIMIT
    assert parameters["ZScoreLimit"]["Default"] == anomaly_defaults.DEFAULT_ZSCORE_LIMIT
    assert parameters["JumpThreshold"]["Default"] == config.DEFAULT_JUMP_THRESHOLD
    assert parameters["JumpThreshold"]["Default"] == anomaly_defaults.DEFAULT_JUMP_THRESHOLD
    assert parameters["IfContamination"]["Default"] == config.DEFAULT_CONTAMINATION
    assert parameters["MaxAnomalies"]["Default"] == config.DEFAULT_MAX_ANOMALIES


def test_deploy_config_lets_sam_create_the_image_repository() -> None:
    deploy = tomllib.loads((ROOT / "samconfig.toml").read_text())["default"]["deploy"]["parameters"]
    assert deploy["resolve_image_repos"] is True


# --- the Analytics API ------------------------------------------------------------------

API = RESOURCES["AnalyticsApi"]["Properties"]
API_EVENTS = {
    name: event["Properties"] for name, event in FUNCTIONS["ApiFunction"]["Events"].items()
}


def test_the_api_serves_exactly_the_five_documented_routes() -> None:
    routes = {(event["Method"], event["Path"]) for event in API_EVENTS.values()}
    assert routes == {
        ("GET", "/datasets"),
        ("GET", "/datasets/{id}"),
        ("GET", "/datasets/{id}/stats"),
        ("GET", "/datasets/{id}/anomalies"),
        ("POST", "/uploads"),
    }
    assert all(event["RestApiId"] == {"Ref": "AnalyticsApi"} for event in API_EVENTS.values())


def test_every_route_requires_the_api_key() -> None:
    for name, event in API_EVENTS.items():
        assert event["Auth"]["ApiKeyRequired"] is True, f"{name} would be open to anyone"


def test_the_key_is_required_per_route_not_for_the_whole_api() -> None:
    """A browser's CORS preflight (OPTIONS) carries no custom headers. If the whole API
    required the key, SAM would require it on the preflight too and every browser call would
    fail with 403. Found by expanding the template; this pins the fix."""
    assert "ApiKeyRequired" not in API["Auth"]
    assert not any(event["Method"] == "OPTIONS" for event in API_EVENTS.values())


def test_the_burst_limit_does_not_exceed_a_new_accounts_lambda_concurrency() -> None:
    """A new AWS account allows 10 concurrent Lambda executions in total. A burst above that
    reaches Lambda, which refuses, and API Gateway shows a 500 instead of a clear 429 (seen
    when 60 simultaneous requests gave 40 of them a 500)."""
    new_account_concurrency = 10
    assert API["Auth"]["UsagePlan"]["Throttle"]["BurstLimit"] <= new_account_concurrency


def test_the_usage_plan_caps_rate_and_volume() -> None:
    plan = API["Auth"]["UsagePlan"]
    assert plan["CreateUsagePlan"] == "PER_API"
    assert plan["Throttle"] == {"RateLimit": 5, "BurstLimit": 10}
    assert plan["Quota"] == {"Limit": 5000, "Period": "DAY"}


def test_the_stage_is_versioned() -> None:
    assert API["StageName"] == "v1"


def test_cors_allows_the_key_header_and_takes_its_origin_from_the_parameter() -> None:
    cors = API["Cors"]
    assert cors["AllowOrigin"] == {"Sub": "'${AllowedOrigin}'"}
    assert "X-Api-Key" in cors["AllowHeaders"] and "Content-Type" in cors["AllowHeaders"]
    assert cors["AllowMethods"] == "'GET,POST,OPTIONS'"
    assert FUNCTIONS["ApiFunction"]["Environment"]["Variables"]["ALLOWED_ORIGIN"] == {
        "Ref": "AllowedOrigin"
    }


def test_the_allowed_origin_default_matches_the_code() -> None:
    default = TEMPLATE["Parameters"]["AllowedOrigin"]["Default"]
    assert default == config.load_api_config({"TABLE_NAME": "t", "RAW_BUCKET": "b"}).allowed_origin


def test_the_api_function_can_only_read_the_table_and_its_index() -> None:
    statements = [
        statement
        for policy in FUNCTIONS["ApiFunction"]["Policies"]
        for statement in policy["Statement"]
        if "dynamodb:GetItem" in statement["Action"]
    ]
    assert len(statements) == 1
    assert statements[0]["Resource"] == [
        {"GetAtt": "PipelineTable.Arn"},
        {"Sub": "${PipelineTable.Arn}/index/GSI1"},  # an index is a separate resource
    ]


@pytest.mark.parametrize(
    "forbidden",
    ["PutItem", "DeleteItem", "UpdateItem", "BatchWriteItem", "Scan", "BatchGetItem"],
)
def test_the_api_function_has_no_other_dynamodb_action(forbidden: str) -> None:
    assert f"dynamodb:{forbidden}" not in dynamodb_actions("ApiFunction")


def test_the_api_function_can_only_write_objects_under_uploads() -> None:
    """It signs upload forms; a form can only do what the signer's own role may do."""
    s3_statements = [
        statement
        for policy in FUNCTIONS["ApiFunction"]["Policies"]
        for statement in policy["Statement"]
        if str(statement["Action"]).startswith("s3:")
    ]
    assert len(s3_statements) == 1
    assert s3_statements[0]["Action"] == "s3:PutObject"
    assert s3_statements[0]["Resource"] == {"Sub": "${RawBucket.Arn}/uploads/*"}


def test_the_api_function_finishes_before_api_gateway_gives_up() -> None:
    """API Gateway cuts an integration off after 29 seconds."""
    assert FUNCTIONS["ApiFunction"]["Timeout"] < 29


def test_the_outputs_give_the_url_and_the_key_id_but_never_the_key() -> None:
    outputs = TEMPLATE["Outputs"]
    assert outputs["ApiKeyId"]["Value"] == {"Ref": "AnalyticsApiApiKey"}
    assert "execute-api" in outputs["ApiUrl"]["Value"]["Sub"] and outputs["ApiUrl"]["Value"][
        "Sub"
    ].endswith("/v1")
    assert "get-api-key" in outputs["ApiKeyId"]["Description"]  # says how to fetch the secret
