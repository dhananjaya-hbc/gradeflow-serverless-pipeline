"""Expands template.yaml with SAM's own translator (the step `sam deploy` runs first).

Some of what a function is allowed to do is not written in the template at all: SAM adds IAM
policies by itself (for example, an `Events: SQS` entry attaches a managed policy that covers every
queue in the account). Only the expanded template shows the roles as AWS will really create them.

Local paths (CodeUri, DefinitionUri) are swapped for dummy S3 locations, which is what `sam package`
does for real. Nothing here talks to AWS.
"""

import copy
import re
from functools import cache
from pathlib import Path
from typing import Any

from samtranslator.parser.parser import Parser
from samtranslator.translator.translator import Translator
from samtranslator.yaml_helper import yaml_parse

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "template.yaml"
BASIC_EXECUTION_POLICY = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
SQS_EXECUTION_POLICY = "arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole"

# SAM looks these names up in IAM when it deploys; here a fixed map stands in for that lookup.
MANAGED_POLICIES = {
    "AWSLambdaBasicExecutionRole": BASIC_EXECUTION_POLICY,
    "AWSLambdaSQSQueueExecutionRole": SQS_EXECUTION_POLICY,
}


@cache
def _expanded() -> dict[str, Any]:
    template = yaml_parse(TEMPLATE_PATH.read_text())
    for resource in template["Resources"].values():
        properties = resource.get("Properties", {})
        if resource["Type"] == "AWS::Serverless::Function":
            if properties.get("PackageType") == "Image":
                properties["ImageUri"] = "111122223333.dkr.ecr.ap-south-1.amazonaws.com/x:y"
            else:
                properties["CodeUri"] = "s3://bucket/key"
        if resource["Type"] == "AWS::Serverless::StateMachine":
            properties["DefinitionUri"] = "s3://bucket/definition.json"
    defaults = {name: p.get("Default", "") for name, p in template.get("Parameters", {}).items()}
    return Translator(MANAGED_POLICIES, Parser()).translate(template, defaults)


def expanded_template() -> dict[str, Any]:
    """The CloudFormation template SAM produces (a copy: callers may not change the cache)."""
    return copy.deepcopy(_expanded())


def readable(node: Any) -> str:
    """A CloudFormation value as short text: `RawBucket.Arn`, `${RawBucket.Arn}/uploads/*`."""
    if isinstance(node, str):
        return node
    if "Ref" in node:
        return str(node["Ref"])
    if "Fn::GetAtt" in node:
        value = node["Fn::GetAtt"]
        return value if isinstance(value, str) else ".".join(value)
    if "Fn::Sub" in node:
        value = node["Fn::Sub"]
        if isinstance(value, str):
            return value
        text, variables = value  # the form SAM's policy templates use: ${name} filled from a map
        return re.sub(
            r"\$\{(\w+)\}",
            lambda m: readable(variables[m.group(1)]) if m.group(1) in variables else m.group(0),
            text,
        )
    raise ValueError(f"unexpected CloudFormation value: {node!r}")


def listed(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def roles() -> dict[str, dict[str, Any]]:
    """Every IAM role in the expanded template, by logical id."""
    return {
        name: resource["Properties"]
        for name, resource in expanded_template()["Resources"].items()
        if resource["Type"] == "AWS::IAM::Role"
    }


def permissions(role: dict[str, Any]) -> set[tuple[str, str]]:
    """The (action, resource) pairs a role's inline policies allow."""
    found: set[tuple[str, str]] = set()
    for policy in role.get("Policies", []):
        for statement in policy["PolicyDocument"]["Statement"]:
            assert statement["Effect"] == "Allow", "roles here only ever allow"
            for action in listed(statement["Action"]):
                for resource in listed(statement["Resource"]):
                    found.add((action, readable(resource)))
    return found
