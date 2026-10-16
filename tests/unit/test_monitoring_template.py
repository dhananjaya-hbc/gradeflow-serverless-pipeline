"""Checks the monitoring added to template.yaml: the dashboard, the alarms and the topic policy.

A typo in a dashboard or alarm is invisible until a deploy, and even then it just shows an empty
chart or an alarm that never fires. So the template is checked against the code: the dashboard
must chart exactly the metrics the handlers publish, and every alarm must cover every function.
"""

import fnmatch
import json
import re
from typing import Any

import pytest
from tests.unit.test_template import FUNCTIONS, RESOURCES, TEMPLATE

from gradeflow.infrastructure.observability import METRICS_NAMESPACE, SERVICE_NAME, MetricName

DASHBOARD = RESOURCES["PipelineDashboard"]["Properties"]
BODY_TEXT: str = DASHBOARD["DashboardBody"]["Sub"]
BODY: dict[str, Any] = json.loads(BODY_TEXT)  # the ${...} placeholders sit inside strings
WIDGETS: list[dict[str, Any]] = BODY["widgets"]
ALARMS = {n: r["Properties"] for n, r in RESOURCES.items() if r["Type"] == "AWS::CloudWatch::Alarm"}
POLICY = RESOURCES["AlertTopicPolicy"]["Properties"]
ALERT_TOPIC = {"Ref": "AlertTopic"}
PSEUDO_PARAMETERS = {"AWS::Region", "AWS::StackName", "AWS::AccountId"}


def metric_widgets() -> list[dict[str, Any]]:
    return [w for w in WIDGETS if w["type"] == "metric"]


def widget_titled(title: str) -> dict[str, Any]:
    (found,) = [w for w in WIDGETS if w["properties"].get("title") == title]
    return found


def metric_specs(widget: dict[str, Any]) -> list[list[Any]]:
    return widget["properties"]["metrics"]


# --- the dashboard -----------------------------------------------------------------------------


def test_the_dashboard_has_a_name_that_starts_with_the_stack_name() -> None:
    assert DASHBOARD["DashboardName"] == {"Sub": "${AWS::StackName}-pipeline"}
    assert len(WIDGETS) >= 10


def test_every_placeholder_in_the_dashboard_points_at_a_real_resource() -> None:
    tokens = set(re.findall(r"\$\{([^}]+)\}", BODY_TEXT))
    assert tokens  # the dashboard really is built from the template's resources
    for token in tokens:
        assert token in PSEUDO_PARAMETERS or token.split(".")[0] in RESOURCES, token


def test_the_dashboard_charts_exactly_the_metrics_the_handlers_publish() -> None:
    """A metric nobody charts is wasted; a name the code never publishes is an empty graph."""
    charted = {
        spec[1]
        for widget in metric_widgets()
        for spec in metric_specs(widget)
        if spec[0] == "GradeFlow"
    }
    assert charted == {metric.value for metric in MetricName}


def test_every_custom_metric_is_charted_with_the_namespace_and_dimension_the_code_uses() -> None:
    specs = [s for w in metric_widgets() for s in metric_specs(w) if s[0].startswith("Grade")]
    assert specs
    for spec in specs:
        # ["GradeFlow", name, "service", "gradeflow", {options}]: the one dimension Powertools adds
        assert spec[0] == METRICS_NAMESPACE
        assert spec[2:4] == ["service", SERVICE_NAME], spec


@pytest.mark.parametrize(
    "title", ["Lambda errors", "Lambda throttles", "Lambda duration (p95, ms)"]
)
def test_the_lambda_charts_cover_every_function(title: str) -> None:
    functions = {
        re.fullmatch(r"\$\{(\w+)\}", spec[3]).group(1)
        for spec in metric_specs(widget_titled(title))
        if spec[0] == "AWS/Lambda"
    }
    assert functions == set(FUNCTIONS)


def test_the_queue_chart_shows_the_upload_queue_and_the_dead_letter_queue() -> None:
    values = {spec[3] for spec in metric_specs(widget_titled("Queues (messages waiting)"))}
    assert values == {"${UploadQueue.QueueName}", "${DeadLetterQueue.QueueName}"}


def test_the_step_functions_chart_uses_the_state_machine_arn() -> None:
    specs = metric_specs(widget_titled("Step Functions runs"))
    assert {spec[1] for spec in specs} == {
        "ExecutionsStarted",
        "ExecutionsSucceeded",
        "ExecutionsFailed",
    }
    assert {spec[3] for spec in specs} == {"${PipelineStateMachine}"}  # a Ref gives the ARN


def test_the_api_chart_and_alarm_find_the_api_by_the_name_the_template_gives_it() -> None:
    assert RESOURCES["AnalyticsApi"]["Properties"]["Name"] == {"Sub": "${AWS::StackName}-api"}
    gateway = [
        spec
        for spec in metric_specs(widget_titled("API requests and errors"))
        if "AWS/ApiGateway" in spec[0]
    ]
    assert len(gateway) == 4
    for spec in gateway:
        assert spec[2:6] == ["ApiName", "${AWS::StackName}-api", "Stage", "v1"]
    assert RESOURCES["AnalyticsApi"]["Properties"]["StageName"] == "v1"


def test_the_alarm_widget_lists_exactly_the_stacks_alarms() -> None:
    (alarm_widget,) = [w for w in WIDGETS if w["type"] == "alarm"]
    assert set(alarm_widget["properties"]["alarms"]) == {f"${{{name}.Arn}}" for name in ALARMS}


def test_the_widgets_fit_the_24_column_grid_and_do_not_overlap() -> None:
    taken: set[tuple[int, int]] = set()
    for widget in WIDGETS:
        x, y, width, height = (widget[k] for k in ("x", "y", "width", "height"))
        assert x >= 0 and x + width <= 24 and width > 0 and height > 0, widget
        cells = {(col, row) for col in range(x, x + width) for row in range(y, y + height)}
        assert not cells & taken, f"overlap at {widget['properties'].get('title')}"
        taken |= cells


def test_every_metric_widget_names_the_region_and_a_period() -> None:
    for widget in metric_widgets():
        props = widget["properties"]
        assert props["region"] == "${AWS::Region}"
        assert props["period"] in (60, 300, 3600)


# --- the alarms --------------------------------------------------------------------------------


def test_there_are_four_alarms_which_stays_inside_the_free_tier() -> None:
    assert set(ALARMS) == {
        "LambdaErrorsAlarm",
        "LambdaThrottlesAlarm",
        "DeadLetterQueueAlarm",
        "ApiServerErrorsAlarm",
    }
    assert len(ALARMS) <= 10  # the first 10 alarms in an account are free


@pytest.mark.parametrize("name", sorted(ALARMS))
def test_every_alarm_notifies_the_alert_topic_and_says_when_it_recovers(name: str) -> None:
    alarm = ALARMS[name]
    assert alarm["AlarmActions"] == [ALERT_TOPIC]
    assert alarm["OKActions"] == [ALERT_TOPIC]
    assert alarm["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
    assert (alarm["EvaluationPeriods"], alarm["DatapointsToAlarm"]) == (1, 1)
    assert alarm["TreatMissingData"] == "notBreaching"  # an idle service is healthy, not broken
    assert alarm["AlarmDescription"].strip()  # says what to do about it


@pytest.mark.parametrize("name", sorted(ALARMS))
def test_every_alarm_name_starts_with_the_stack_name(name: str) -> None:
    """The topic policy only lets alarms named like this publish."""
    assert ALARMS[name]["AlarmName"]["Sub"].startswith("${AWS::StackName}-")


@pytest.mark.parametrize(
    ("alarm", "metric"),
    [("LambdaErrorsAlarm", "Errors"), ("LambdaThrottlesAlarm", "Throttles")],
)
def test_the_lambda_alarms_watch_every_function_at_once(alarm: str, metric: str) -> None:
    entries = ALARMS[alarm]["Metrics"]
    (expression,) = [e for e in entries if "Expression" in e]
    assert expression["Expression"] == "SUM(METRICS())"  # any function, any error
    assert expression["ReturnData"] is True
    stats = [e for e in entries if "MetricStat" in e]
    assert all(e["ReturnData"] is False for e in stats)  # only the sum is judged
    for entry in stats:
        stat = entry["MetricStat"]
        assert stat["Metric"]["Namespace"] == "AWS/Lambda"
        assert stat["Metric"]["MetricName"] == metric
        assert (stat["Period"], stat["Stat"]) == (300, "Sum")
    watched = {e["MetricStat"]["Metric"]["Dimensions"][0]["Value"]["Ref"] for e in stats}
    assert watched == set(FUNCTIONS)  # a function added later without an alarm would fail here
    assert len({e["Id"] for e in entries}) == len(entries)  # ids are unique
    # CloudWatch allows at most 10 metrics in one alarm. With 8 functions plus the sum there is
    # room for one more; a 10th function needs a second alarm, and would otherwise fail a deploy.
    assert len(entries) <= 10
    assert ALARMS[alarm]["Threshold"] == 1


def test_the_dead_letter_queue_alarm_fires_on_a_single_message() -> None:
    alarm = ALARMS["DeadLetterQueueAlarm"]
    assert (alarm["Namespace"], alarm["MetricName"]) == (
        "AWS/SQS",
        "ApproximateNumberOfMessagesVisible",
    )
    assert alarm["Dimensions"] == [
        {"Name": "QueueName", "Value": {"GetAtt": "DeadLetterQueue.QueueName"}}
    ]
    assert (alarm["Statistic"], alarm["Threshold"]) == ("Maximum", 1)


def test_the_api_alarm_counts_every_5xx_the_gateway_returns() -> None:
    """Not our custom metric: throttled requests never reach our code but are still 5XX."""
    alarm = ALARMS["ApiServerErrorsAlarm"]
    assert (alarm["Namespace"], alarm["MetricName"]) == ("AWS/ApiGateway", "5XXError")
    assert alarm["Dimensions"] == [
        {"Name": "ApiName", "Value": {"Sub": "${AWS::StackName}-api"}},
        {"Name": "Stage", "Value": "v1"},
    ]
    assert (alarm["Statistic"], alarm["Threshold"]) == ("Sum", 3)


# --- who may publish to the alert topic --------------------------------------------------------


def test_only_this_stacks_alarms_may_publish_to_the_alert_topic() -> None:
    assert POLICY["Topics"] == [ALERT_TOPIC]
    (statement,) = POLICY["PolicyDocument"]["Statement"]
    assert statement["Effect"] == "Allow"
    assert statement["Principal"] == {"Service": "cloudwatch.amazonaws.com"}  # never "*"
    assert statement["Action"] == "sns:Publish"  # not Subscribe, SetTopicAttributes, ...
    assert statement["Resource"] == ALERT_TOPIC
    condition = statement["Condition"]
    assert condition["StringEquals"] == {"aws:SourceAccount": {"Ref": "AWS::AccountId"}}
    assert condition["ArnLike"]["aws:SourceArn"] == {
        "Sub": "arn:aws:cloudwatch:${AWS::Region}:${AWS::AccountId}:alarm:${AWS::StackName}-*"
    }


@pytest.mark.parametrize("name", sorted(ALARMS))
def test_every_alarm_matches_the_pattern_the_topic_policy_allows(name: str) -> None:
    """A renamed alarm that no longer matched would be refused by SNS: silently never notify."""
    pattern = "arn:aws:cloudwatch:ap-south-1:123456789012:alarm:gradeflow-*"
    alarm_name = ALARMS[name]["AlarmName"]["Sub"].replace("${AWS::StackName}", "gradeflow")
    arn = f"arn:aws:cloudwatch:ap-south-1:123456789012:alarm:{alarm_name}"
    assert fnmatch.fnmatch(arn, pattern)


# --- logging settings and outputs --------------------------------------------------------------


def test_powertools_settings_match_what_the_code_uses() -> None:
    variables = TEMPLATE["Globals"]["Function"]["Environment"]["Variables"]
    assert variables["POWERTOOLS_SERVICE_NAME"] == SERVICE_NAME
    assert variables["POWERTOOLS_METRICS_NAMESPACE"] == METRICS_NAMESPACE
    assert variables["POWERTOOLS_LOG_LEVEL"] == {"Ref": "LogLevel"}


def test_no_function_overrides_the_shared_powertools_settings() -> None:
    for name, properties in FUNCTIONS.items():
        variables = properties.get("Environment", {}).get("Variables", {})
        assert not [v for v in variables if v.startswith("POWERTOOLS_")], name


def test_the_log_level_parameter_offers_only_real_levels_and_defaults_to_info() -> None:
    parameter = TEMPLATE["Parameters"]["LogLevel"]
    assert parameter["Default"] == "INFO"
    assert parameter["AllowedValues"] == ["DEBUG", "INFO", "WARNING", "ERROR"]


def test_the_dashboard_has_an_output_with_its_address() -> None:
    value = TEMPLATE["Outputs"]["PipelineDashboardUrl"]["Value"]["Sub"]
    assert "${PipelineDashboard}" in value
    assert value.startswith("https://${AWS::Region}.console.aws.amazon.com/cloudwatch/")
