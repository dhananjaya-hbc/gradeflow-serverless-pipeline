"""deploy/github-oidc-role.yaml: who may deploy, what they may do, and that nothing can escalate.

The template makes two roles. These tests pin the trust (exactly one repository and branch, no
wildcards), the scope of every permission, and the ways a deploy role could grow its own power. They
also check the permissions against the API calls CloudTrail recorded during the real deploys of this
stack (tests/fixtures/deploy_calls_observed.json), and against the resource types the stack uses.
"""

import fnmatch
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.unit.sam_expand import expanded_template
from tests.unit.test_template import _TemplateLoader

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE: dict[str, Any] = yaml.load(
    (ROOT / "deploy" / "github-oidc-role.yaml").read_text(), Loader=_TemplateLoader
)
RESOURCES: dict[str, dict[str, Any]] = TEMPLATE["Resources"]
PARAMETERS: dict[str, dict[str, Any]] = TEMPLATE["Parameters"]
OBSERVED: dict[str, Any] = json.loads(
    (ROOT / "tests/fixtures/deploy_calls_observed.json").read_text()
)

GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"
MAIN_STACK_ARN = "arn:aws:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/${StackName}/*"
DEPLOY_ROLE = RESOURCES["DeployRole"]["Properties"]
EXEC_ROLE = RESOURCES["ExecutionRole"]["Properties"]
EXEC_POLICIES = ["ExecutionComputePolicy", "ExecutionDataPolicy", "ExecutionEdgePolicy"]


def statements(policy: str) -> list[dict[str, Any]]:
    return RESOURCES[policy]["Properties"]["PolicyDocument"]["Statement"]


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def text(value: Any) -> str:
    """A CloudFormation string or {"Sub": ...} / {"GetAtt": ...} / {"Ref": ...} as plain text."""
    if isinstance(value, str):
        return value
    return next(iter(value.values())) if isinstance(next(iter(value.values())), str) else "?"


def allowed(policies: list[str]) -> list[tuple[str, str, dict[str, Any]]]:
    """(action, resource text, statement) for every Allow in the policies."""
    found = []
    for policy in policies:
        for statement in statements(policy):
            if statement["Effect"] != "Allow":
                continue
            for action in as_list(statement["Action"]):
                for resource in as_list(statement["Resource"]):
                    found.append((action, text(resource), statement))
    return found


EXEC_ALLOWED = allowed(EXEC_POLICIES)
DEPLOY_ALLOWED = allowed(["DeployPolicy"])


def permits(grants: list[tuple[str, str, dict[str, Any]]], action: str) -> bool:
    return any(fnmatch.fnmatchcase(action.lower(), g[0].lower()) for g in grants)


# --- who may assume the deploy role --------------------------------------------------------------


def test_only_github_actions_of_one_repository_and_one_branch_can_assume_the_deploy_role() -> None:
    (statement,) = DEPLOY_ROLE["AssumeRolePolicyDocument"]["Statement"]
    assert statement["Effect"] == "Allow"
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert statement["Principal"] == {
        "Federated": {"Sub": "arn:aws:iam::${AWS::AccountId}:oidc-provider/" + GITHUB_OIDC_HOST}
    }
    condition = statement["Condition"]
    assert list(condition) == [
        "StringEquals"
    ]  # StringEquals only: no pattern matching, no wildcards
    equals = condition["StringEquals"]
    assert set(equals) == {
        "token.actions.githubusercontent.com:aud",
        "token.actions.githubusercontent.com:sub",
    }
    assert equals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"


def evaluate(node: Any, params: dict[str, str]) -> str:
    """Resolve the few CloudFormation functions the trust policy's subject is built from."""
    if isinstance(node, str):
        return node
    ((function, argument),) = node.items()
    if function == "Ref":
        return params[argument]
    if function == "Split":
        delimiter, source = argument
        return evaluate(source, params).split(delimiter)  # type: ignore[return-value]
    if function == "Select":
        index, source = argument
        values = evaluate(source, params) if isinstance(source, dict) else source
        return values[int(index)]
    if function == "Sub":
        template, variables = argument
        scope = {**params, **{k: evaluate(v, params) for k, v in variables.items()}}
        return re.sub(r"\$\{(\w+)\}", lambda m: scope[m.group(1)], template)
    raise ValueError(f"unsupported function {function}")


def default_parameters() -> dict[str, str]:
    return {name: str(p["Default"]) for name, p in PARAMETERS.items() if "Default" in p}


def trust_subject(**overrides: str) -> str:
    (statement,) = DEPLOY_ROLE["AssumeRolePolicyDocument"]["Statement"]
    sub = statement["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"]
    return evaluate(sub, {**default_parameters(), **overrides})


# The subject GitHub put in the token of the first real deploy run, as recorded by CloudTrail. This
# repository uses GitHub's immutable subject (numeric owner and repository ids after the names);
# a trust policy written for the plain `repo:owner/repo:...` form refuses it.
REAL_SUBJECT = (
    "repo:dhananjaya-hbc@171498451/gradeflow-serverless-pipeline@1387779260:ref:refs/heads/main"
)


def test_the_trust_subject_is_exactly_the_one_github_sent_on_the_first_real_run() -> None:
    assert trust_subject() == REAL_SUBJECT


def test_the_subject_carries_the_numeric_ids_so_a_recreated_or_renamed_repository_is_refused() -> (
    None
):
    subject = trust_subject()
    assert re.fullmatch(r"repo:[^@/]+@[0-9]+/[^@/]+@[0-9]+:ref:refs/heads/[^*]+", subject)
    assert "@" in subject  # the plain form, repo:owner/repo, would let a repo of the same name in
    assert trust_subject(GitHubRepositoryId="42") != subject
    assert trust_subject(GitHubOwnerId="42") != subject


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"GitHubBranch": "release"},
            "repo:dhananjaya-hbc@171498451/gradeflow-serverless-pipeline@1387779260:ref:refs/heads/release",
        ),
        (
            {"GitHubRepository": "o/r", "GitHubOwnerId": "1", "GitHubRepositoryId": "2"},
            "repo:o@1/r@2:ref:refs/heads/main",
        ),
    ],
)
def test_the_subject_follows_the_parameters(overrides: dict[str, str], expected: str) -> None:
    assert trust_subject(**overrides) == expected


def test_the_default_ids_are_the_ones_github_reports_for_this_repository() -> None:
    """Asks GitHub when `gh` is installed and signed in; skipped otherwise (needs the network)."""
    repo = PARAMETERS["GitHubRepository"]["Default"]
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}", "--jq", r'"\(.owner.id) \(.id)"'],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("gh is not available")
    if result.returncode != 0:
        pytest.skip("gh could not reach GitHub")
    owner_id, repository_id = result.stdout.split()
    assert PARAMETERS["GitHubOwnerId"]["Default"] == owner_id
    assert PARAMETERS["GitHubRepositoryId"]["Default"] == repository_id


def test_the_subject_never_contains_a_wildcard_or_a_pull_request_or_environment_part() -> None:
    subject = trust_subject()
    assert "*" not in subject and "pull_request" not in subject and "environment" not in subject


def test_the_template_reuses_the_accounts_github_provider_and_does_not_create_one() -> None:
    """An account can hold only one provider for this address, and one already exists."""
    assert not [r for r in RESOURCES.values() if r["Type"] == "AWS::IAM::OIDCProvider"]


def test_the_default_repository_is_the_one_this_checkout_pushes_to() -> None:
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True
    )
    if remote.returncode != 0:
        pytest.skip("no git remote")
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(\.git)?$", remote.stdout.strip())
    assert match, remote.stdout
    assert PARAMETERS["GitHubRepository"]["Default"] == match.group(1)


@pytest.mark.parametrize(
    ("parameter", "good", "bad"),
    [
        (
            "GitHubRepository",
            ["dhananjaya-hbc/gradeflow-serverless-pipeline", "a/b"],
            ["*", "owner/*", "*/repo", "owner", "owner/repo/extra", "owner/re*po", ""],
        ),
        ("GitHubOwnerId", ["171498451", "1"], ["", "abc", "12 3", "-1", "*", "12.5"]),
        ("GitHubRepositoryId", ["1387779260", "7"], ["", "abc", "12 3", "-1", "*", "12.5"]),
        ("GitHubBranch", ["main", "release/1.0"], ["*", "main*", "refs/*", "ma?n", "", "a b"]),
        ("StackName", ["gradeflow", "my-stack-2"], ["Grade", "-x", "a--b", "a_b", "*", ""]),
        ("ManagedLayersAccountId", ["336392948345"], ["33639294834", "abc", "*", ""]),
    ],
)
def test_the_parameters_refuse_wildcards_and_malformed_values(
    parameter: str, good: list[str], bad: list[str]
) -> None:
    pattern = re.compile(PARAMETERS[parameter]["AllowedPattern"])
    for value in good:
        assert pattern.fullmatch(value), value
    for value in bad:
        assert not pattern.fullmatch(value), value


def test_only_cloudformation_of_this_account_and_stack_can_assume_the_execution_role() -> None:
    (statement,) = EXEC_ROLE["AssumeRolePolicyDocument"]["Statement"]
    assert statement["Principal"] == {"Service": "cloudformation.amazonaws.com"}
    assert statement["Action"] == "sts:AssumeRole"
    assert statement["Condition"] == {
        "StringEquals": {"aws:SourceAccount": {"Ref": "AWS::AccountId"}},
        "ArnLike": {"aws:SourceArn": {"Sub": MAIN_STACK_ARN}},
    }


@pytest.mark.parametrize("role", [DEPLOY_ROLE, EXEC_ROLE], ids=["deploy", "execution"])
def test_sessions_are_short(role: dict[str, Any]) -> None:
    assert role["MaxSessionDuration"] <= 3600


# --- the CI roles cannot reach themselves ---------------------------------------------------------


def test_the_ci_roles_are_named_outside_the_prefix_the_execution_role_may_manage() -> None:
    """The execution role may create and change IAM roles called <stack>-*. A CI role with that
    prefix could be rewritten by it: a way for a deploy to give itself more power."""
    names = [DEPLOY_ROLE["RoleName"]["Sub"], EXEC_ROLE["RoleName"]["Sub"]]
    assert names == ["ci-${StackName}-deploy", "ci-${StackName}-cfn-exec"]
    managed = [r for a, r, _ in EXEC_ALLOWED if a.startswith("iam:") and ":role/" in r]
    assert managed and all(r.endswith(":role/${StackName}-*") for r in managed)
    for name in ("ci-gradeflow-deploy", "ci-gradeflow-cfn-exec"):
        assert not fnmatch.fnmatchcase(name, "gradeflow-*")


def test_the_execution_role_is_explicitly_denied_every_change_to_the_ci_roles() -> None:
    (deny,) = [s for s in statements("ExecutionComputePolicy") if s["Effect"] == "Deny"]
    assert text(deny["Resource"]) == "arn:aws:iam::${AWS::AccountId}:role/ci-${StackName}-*"
    read_only = {
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:GetAccountSummary",
    }
    writes = {a for a, _, _ in EXEC_ALLOWED if a.startswith("iam:")} - read_only
    assert writes <= set(
        deny["Action"]
    )  # every way it may change a role is denied for the CI roles
    assert "iam:PassRole" in deny["Action"]  # it cannot hand a CI role to a service either


def test_the_deploy_role_can_do_nothing_in_iam_except_hand_over_the_execution_role() -> None:
    iam = [(a, r, s) for a, r, s in DEPLOY_ALLOWED if a.startswith("iam:")]
    assert {a for a, _, _ in iam} == {"iam:PassRole"}
    ((_, resource, statement),) = [(a, r, s) for a, r, s in iam]
    assert resource == "ExecutionRole.Arn"
    assert statement["Condition"] == {
        "StringEquals": {"iam:PassedToService": "cloudformation.amazonaws.com"}
    }


def test_the_execution_role_can_pass_roles_only_to_lambda_and_step_functions() -> None:
    (statement,) = [
        s for s in statements("ExecutionComputePolicy") if s["Sid"].startswith("PassItsRoles")
    ]
    assert statement["Condition"]["StringEquals"]["iam:PassedToService"] == [
        "lambda.amazonaws.com",
        "states.amazonaws.com",
    ]
    assert text(statement["Resource"]).endswith(":role/${StackName}-*")


def test_the_execution_role_attaches_only_the_basic_logging_policy() -> None:
    (statement,) = [
        s for s in statements("ExecutionComputePolicy") if s["Sid"].startswith("AttachOnly")
    ]
    assert statement["Condition"] == {
        "ArnEquals": {
            "iam:PolicyARN": "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
        }
    }


@pytest.mark.parametrize(
    "forbidden",
    [
        "iam:CreateUser",
        "iam:CreateAccessKey",
        "iam:CreatePolicy",
        "iam:CreatePolicyVersion",
        "iam:SetDefaultPolicyVersion",
        "iam:AddUserToGroup",
        "iam:CreateLoginProfile",
        "iam:PutRolePermissionsBoundary",
        "iam:DeleteRolePermissionsBoundary",
        "sts:AssumeRole",
        "organizations:*",
    ],
)
def test_no_role_can_create_identities_or_policies_or_assume_other_roles(forbidden: str) -> None:
    assert not permits(EXEC_ALLOWED + DEPLOY_ALLOWED, forbidden), forbidden


# --- scope of the deploy role ---------------------------------------------------------------------


def test_the_deploy_role_touches_only_these_services() -> None:
    services = {a.split(":")[0] for a, _, _ in DEPLOY_ALLOWED}
    assert services == {"cloudformation", "iam", "s3", "ecr", "cloudfront", "apigateway"}
    # not a single direct permission on the application's own resources
    for service in ("lambda", "dynamodb", "sqs", "sns", "states", "logs", "cloudwatch", "kms"):
        assert service not in services


def test_the_deploy_role_has_no_wildcard_action() -> None:
    assert [a for a, _, _ in DEPLOY_ALLOWED if "*" in a] == []


def test_the_deploy_role_uses_resource_star_only_where_aws_offers_nothing_narrower() -> None:
    wide = {a for a, r, _ in DEPLOY_ALLOWED if r == "*"}
    assert wide == {"cloudformation:GetTemplateSummary", "ecr:GetAuthorizationToken"}


def test_the_deploy_role_changes_only_this_stack_and_sams_companion_stack() -> None:
    cloudformation = {
        r for a, r, _ in DEPLOY_ALLOWED if a.startswith("cloudformation:") and r != "*"
    }
    assert cloudformation == {
        "arn:aws:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/${StackName}/*",
        "arn:aws:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/${StackName}-*-CompanionStack/*",
        "arn:aws:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/aws-sam-cli-managed-default/*",
        "arn:aws:cloudformation:${AWS::Region}:aws:transform/Serverless-2016-10-31",
    }
    deletes = {a for a, _, _ in DEPLOY_ALLOWED if a.startswith("cloudformation:Delete")}
    assert deletes == {"cloudformation:DeleteChangeSet"}  # it can never delete a stack


def test_only_the_companion_stack_can_be_created_or_updated_directly() -> None:
    direct = [
        (a, r)
        for a, r, _ in DEPLOY_ALLOWED
        if a in ("cloudformation:CreateStack", "cloudformation:UpdateStack")
    ]
    assert direct and all(r.endswith("-*-CompanionStack/*") for _, r in direct)


def test_the_deploy_role_uploads_only_to_sams_bucket_and_the_dashboard_bucket() -> None:
    writes = {r for a, r, _ in DEPLOY_ALLOWED if a in ("s3:PutObject", "s3:DeleteObject")}
    assert writes == {
        "arn:aws:s3:::aws-sam-cli-managed-default-samclisourcebucket-*/*",
        "arn:aws:s3:::${StackName}-site-${AWS::AccountId}-${AWS::Region}/*",
    }
    deletes = {r for a, r, _ in DEPLOY_ALLOWED if a == "s3:DeleteObject"}
    assert deletes == {"arn:aws:s3:::${StackName}-site-${AWS::AccountId}-${AWS::Region}/*"}


def test_the_deploy_role_cannot_read_or_write_the_applications_data() -> None:
    for action in ("s3:GetObject",):
        resources = {r for a, r, _ in DEPLOY_ALLOWED if a == action}
        assert resources == {"arn:aws:s3:::aws-sam-cli-managed-default-samclisourcebucket-*/*"}
    for action in (
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query",
        "sqs:ReceiveMessage",
        "sqs:SendMessage",
        "lambda:InvokeFunction",
        "states:StartExecution",
        "sns:Publish",
    ):
        assert not permits(DEPLOY_ALLOWED, action), action


def test_the_deploy_role_reads_the_api_key_and_invalidates_the_cache_and_nothing_else() -> None:
    assert [(a, r) for a, r, _ in DEPLOY_ALLOWED if a.startswith("apigateway:")] == [
        ("apigateway:GET", "arn:aws:apigateway:${AWS::Region}::/apikeys/*")
    ]
    assert [a for a, _, _ in DEPLOY_ALLOWED if a.startswith("cloudfront:")] == [
        "cloudfront:CreateInvalidation"
    ]


def test_the_deploy_role_pushes_images_only_to_repositories_of_this_stack() -> None:
    repos = {
        r for a, r, _ in DEPLOY_ALLOWED if a.startswith("ecr:") and a != "ecr:GetAuthorizationToken"
    }
    assert repos == {"arn:aws:ecr:${AWS::Region}:${AWS::AccountId}:repository/${StackName}*"}


# --- scope of the execution role ------------------------------------------------------------------

# Statements whose resources do not contain the stack name, and why. Anything else that does not
# contain it is a mistake.
RESOURCE_EXCEPTIONS = {
    "ManageQueueMappings": "mappings have random ids",
    "UseThePublishedPandasLayer": "the layer is published by an AWS-owned account",
    "LetLambdaEncryptEnvironmentVariablesWithItsDefaultKey": "AWS-managed key, only via Lambda",
    "ManageTheWorkflow": "CloudFormation names it after its logical id (PipelineStateMachine-*)",
    "ReadTheBuildArtifacts": "SAM's artifact bucket, read only",
    "RunTheSamTransform": "the SAM transform is an AWS-owned resource",
    "ManageTheRestApiKeyAndUsagePlan": "API Gateway uses random ids",
    "ChangeTheDistributionAndItsParts": "CloudFront uses random ids",
}
# Actions that accept no resource narrower than "*".
WILDCARD_RESOURCE_ACTIONS = {
    "iam:GetAccountSummary",
    "lambda:GetAccountSettings",
    "lambda:ListEventSourceMappings",
    "states:CreateStateMachine",
    "logs:DescribeLogGroups",
    "logs:DescribeIndexPolicies",
    "logs:DescribeResourcePolicies",
    "cloudwatch:DescribeAlarms",
    "cloudfront:CreateDistribution",
    "cloudfront:CreateOriginAccessControl",
    "cloudfront:CreateResponseHeadersPolicy",
    "cloudfront:TagResource",
    "lambda:CreateEventSourceMapping",  # limited by the function instead (a condition)
    "sns:Unsubscribe",
    "sns:GetSubscriptionAttributes",
    "sns:SetSubscriptionAttributes",
}
# The only wildcard actions: reads and writes of a bucket's own settings, never its objects.
WILDCARD_ACTIONS = {
    "s3:GetBucket*",
    "s3:PutBucket*",
    "s3:Get*Configuration",
    "s3:Put*Configuration",
}


def test_the_execution_role_uses_resource_star_only_for_the_pinned_actions() -> None:
    wide = {a for a, r, _ in EXEC_ALLOWED if r == "*"}
    assert wide == WILDCARD_RESOURCE_ACTIONS


def test_every_execution_statement_is_limited_to_the_stacks_name_or_a_documented_exception() -> (
    None
):
    for policy in EXEC_POLICIES:
        for statement in statements(policy):
            if statement["Effect"] != "Allow":
                continue
            resources = [text(r) for r in as_list(statement["Resource"])]
            if resources == ["*"]:
                continue  # pinned action by action above
            if statement["Sid"] in RESOURCE_EXCEPTIONS:
                continue
            assert all("${StackName}" in r for r in resources), (statement["Sid"], resources)


def test_the_execution_role_may_use_the_sam_transform_and_nothing_else_in_cloudformation() -> None:
    """The first CI deploy failed with: the execution role "is not authorized to perform:
    cloudformation:CreateChangeSet on resource:
    arn:aws:cloudformation:ap-south-1:aws:transform/Serverless-2016-10-31". With a service role,
    CloudFormation checks that permission on the role. It is needed there, and nothing more."""
    cloudformation = {(a, r) for a, r, _ in EXEC_ALLOWED if a.startswith("cloudformation:")}
    assert cloudformation == {
        (
            "cloudformation:CreateChangeSet",
            "arn:aws:cloudformation:${AWS::Region}:aws:transform/Serverless-2016-10-31",
        )
    }


# Actions IAM cannot limit to a resource. A statement that names an ARN never matches them, so they
# work only under Resource "*". Granted any other way they are dead: allowed on paper, refused in
# practice. (Found by running every granted action through IAM's policy simulator against the live
# role: the SNS subscription actions and CreateEventSourceMapping had been listed under ARNs.)
NO_RESOURCE_LEVEL_SUPPORT = {
    "sns:Unsubscribe",
    "sns:GetSubscriptionAttributes",
    "sns:SetSubscriptionAttributes",
    "lambda:CreateEventSourceMapping",
}


def test_actions_without_resource_level_support_are_granted_only_on_star() -> None:
    for action in NO_RESOURCE_LEVEL_SUPPORT:
        resources = {r for a, r, _ in EXEC_ALLOWED if a == action}
        assert resources == {"*"}, f"{action} would never match: {resources}"


def test_creating_a_queue_mapping_is_limited_to_the_stacks_functions_by_a_condition() -> None:
    (statement,) = [
        s
        for s in statements("ExecutionComputePolicy")
        if s["Sid"] == "CreateQueueMappingsForTheStacksFunctions"
    ]
    assert statement["Action"] == "lambda:CreateEventSourceMapping"
    assert statement["Condition"] == {
        "ArnLike": {
            "lambda:FunctionArn": {
                "Sub": "arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${StackName}-*"
            }
        }
    }


def test_the_email_subscription_actions_are_in_their_own_star_statement() -> None:
    (statement,) = [
        s for s in statements("ExecutionDataPolicy") if s["Sid"] == "ManageTheEmailSubscription"
    ]
    assert set(statement["Action"]) == {
        "sns:Unsubscribe",
        "sns:GetSubscriptionAttributes",
        "sns:SetSubscriptionAttributes",
    }
    assert statement["Resource"] == "*"


def test_the_documented_exceptions_are_real_statements() -> None:
    sids = {s["Sid"] for policy in EXEC_POLICIES for s in statements(policy)}
    assert set(RESOURCE_EXCEPTIONS) <= sids


def test_the_only_wildcard_actions_are_the_bucket_setting_patterns() -> None:
    assert {a for a, _, _ in EXEC_ALLOWED if "*" in a} == WILDCARD_ACTIONS


def test_wildcard_bucket_actions_apply_to_the_stacks_buckets_only() -> None:
    resources = {r for a, r, _ in EXEC_ALLOWED if a in WILDCARD_ACTIONS}
    assert resources == {"arn:aws:s3:::${StackName}-*"}


@pytest.mark.parametrize(
    "data_action",
    [
        "s3:GetObject",  # except the build artifacts, checked below
        "s3:PutObject",
        "s3:DeleteObject",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:DeleteItem",
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sns:Publish",
        "lambda:InvokeFunction",
        "states:StartExecution",
        "logs:GetLogEvents",
        "logs:FilterLogEvents",
    ],
)
def test_the_execution_role_builds_infrastructure_but_cannot_touch_the_applications_data(
    data_action: str,
) -> None:
    resources = {
        r for a, r, _ in EXEC_ALLOWED if fnmatch.fnmatchcase(data_action.lower(), a.lower())
    }
    if data_action == "s3:GetObject":
        assert resources == {"arn:aws:s3:::aws-sam-cli-managed-default-samclisourcebucket-*/*"}
    else:
        assert resources == set(), data_action


@pytest.mark.parametrize("policy", EXEC_POLICIES + ["DeployPolicy"])
def test_a_managed_policy_stays_inside_the_6144_character_limit(policy: str) -> None:
    """AWS rejects a larger one at deploy time. Placeholders are longer than the real values."""
    compact = json.dumps(RESOURCES[policy]["Properties"]["PolicyDocument"], separators=(",", ":"))
    assert len(compact) <= 6144, len(compact)


def test_a_role_has_at_most_ten_managed_policies() -> None:
    assert len(EXEC_ROLE["ManagedPolicyArns"]) <= 10
    assert len(DEPLOY_ROLE["ManagedPolicyArns"]) <= 10


@pytest.mark.parametrize("policy", EXEC_POLICIES + ["DeployPolicy"])
def test_statement_ids_are_unique_and_every_statement_is_explained(policy: str) -> None:
    sids = [s["Sid"] for s in statements(policy)]
    assert len(sids) == len(set(sids))
    assert all(re.fullmatch(r"[A-Za-z0-9]+", sid) for sid in sids)


# --- against what the real deploys did ------------------------------------------------------------

S3_NAMES = {
    "GetBucketLifecycle": "GetLifecycleConfiguration",
    "PutBucketLifecycle": "PutLifecycleConfiguration",
    "GetBucketEncryption": "GetEncryptionConfiguration",
    "PutBucketEncryption": "PutEncryptionConfiguration",
    "GetBucketCors": "GetBucketCORS",
    "PutBucketCors": "PutBucketCORS",
    "GetBucketReplication": "GetReplicationConfiguration",
    "GetBucketAnalyticsConfiguration": "GetAnalyticsConfiguration",
    "GetBucketInventoryConfiguration": "GetInventoryConfiguration",
    "GetBucketMetricsConfiguration": "GetMetricsConfiguration",
    "GetBucketIntelligentTieringConfiguration": "GetIntelligentTieringConfiguration",
}
HTTP_VERB = {"Create": "POST", "Get": "GET", "Put": "PUT", "Update": "PATCH", "Delete": "DELETE"}


def iam_actions(source: str, name: str) -> list[str]:
    """The IAM action(s) that authorise a CloudTrail event (the two vocabularies differ)."""
    if source == "apigateway":  # API Gateway's IAM actions are the HTTP verbs
        return [f"apigateway:{HTTP_VERB[re.match(r'[A-Z][a-z]+', name).group(0)]}"]
    if source == "lambda":  # event names carry an API date: CreateFunction20150331
        return [f"lambda:{re.sub(r'[0-9]{8}(v[0-9]+)?$', '', name)}"]
    if source == "cloudfront" and name == "CreateDistributionWithTags":
        return ["cloudfront:CreateDistribution", "cloudfront:TagResource"]
    if source == "s3":
        return [f"s3:{S3_NAMES.get(name, name)}"]
    return [f"{ {'monitoring': 'cloudwatch'}.get(source, source) }:{name}".replace(" ", "")]


def test_the_name_mapping_handles_the_awkward_cases() -> None:
    assert iam_actions("lambda", "CreateFunction20150331") == ["lambda:CreateFunction"]
    assert iam_actions("lambda", "GetFunction20150331v2") == ["lambda:GetFunction"]
    assert iam_actions("apigateway", "UpdateRestApi") == ["apigateway:PATCH"]
    assert iam_actions("monitoring", "PutMetricAlarm") == ["cloudwatch:PutMetricAlarm"]
    assert iam_actions("s3", "PutBucketLifecycle") == ["s3:PutLifecycleConfiguration"]
    assert iam_actions("cloudfront", "CreateDistributionWithTags") == [
        "cloudfront:CreateDistribution",
        "cloudfront:TagResource",
    ]


def missing(calls: list[list[str]], grants: list[tuple[str, str, dict[str, Any]]]) -> list[str]:
    return sorted(
        f"{source}:{name} -> {action}"
        for source, name in calls
        for action in iam_actions(source, name)
        if not permits(grants, action)
    )


def test_the_execution_role_allows_every_call_cloudformation_made_in_the_real_deploys() -> None:
    assert len(OBSERVED["made_by_cloudformation"]) > 100  # the evidence is really there
    assert missing(OBSERVED["made_by_cloudformation"], EXEC_ALLOWED) == []


def test_the_execution_role_allows_what_it_was_denied_in_the_first_successful_ci_deploy() -> None:
    """Real denials, named by IAM itself in the error message (so no guessing which action it was):
    CloudFormation's read of the dashboard bucket's tags was refused until s3:ListTagsForResource
    was granted."""
    denied = OBSERVED["denied_to_the_execution_role_in_the_first_successful_ci_deploy"][
        "iam_actions"
    ]
    assert denied  # the evidence is there
    assert [a for a in denied if not permits(EXEC_ALLOWED, a)] == []


def test_reading_and_writing_bucket_tags_is_limited_to_the_stacks_buckets() -> None:
    for action in ("s3:ListTagsForResource", "s3:TagResource", "s3:UntagResource"):
        resources = {r for a, r, _ in EXEC_ALLOWED if a == action}
        assert resources == {"arn:aws:s3:::${StackName}-*"}, action


def test_the_execution_role_allows_what_lambda_does_for_the_caller() -> None:
    calls = OBSERVED["made_by_the_lambda_service_for_the_caller"]
    assert {"kms", "ecr"} == {source for source, _ in calls}
    assert missing(calls, EXEC_ALLOWED) == []


def test_the_deploy_role_allows_every_call_sam_cli_made_with_the_callers_credentials() -> None:
    calls = [c for c in OBSERVED["made_by_sam_cli"] if c[0] != "sts"]  # STS needs no permission
    assert len(calls) >= 8
    assert missing(calls, DEPLOY_ALLOWED) == []


def test_the_deploy_role_allows_the_calls_of_the_image_push() -> None:
    calls = OBSERVED["made_by_the_ecr_service_during_the_image_push"]
    assert len(calls) == 6
    assert missing(calls, DEPLOY_ALLOWED) == []


# --- against what the stack contains --------------------------------------------------------------

# For every resource type the application stack uses: what CloudFormation needs to create, change
# and delete it. CloudTrail only shows what the past deploys did, so deletes and replacements are
# listed here, from the service documentation.
REQUIRED_FOR_TYPE: dict[str, list[str]] = {
    "AWS::ApiGateway::ApiKey": [
        "apigateway:POST",
        "apigateway:GET",
        "apigateway:PATCH",
        "apigateway:DELETE",
    ],
    "AWS::ApiGateway::Deployment": ["apigateway:POST", "apigateway:GET", "apigateway:DELETE"],
    "AWS::ApiGateway::RestApi": [
        "apigateway:POST",
        "apigateway:PUT",
        "apigateway:PATCH",
        "apigateway:DELETE",
    ],
    "AWS::ApiGateway::Stage": ["apigateway:POST", "apigateway:PATCH", "apigateway:DELETE"],
    "AWS::ApiGateway::UsagePlan": ["apigateway:POST", "apigateway:PATCH", "apigateway:DELETE"],
    "AWS::ApiGateway::UsagePlanKey": ["apigateway:POST", "apigateway:DELETE"],
    "AWS::CloudFront::Distribution": [
        "cloudfront:CreateDistribution",
        "cloudfront:UpdateDistribution",
        "cloudfront:DeleteDistribution",
        "cloudfront:GetDistribution",
        "cloudfront:TagResource",
    ],
    "AWS::CloudFront::OriginAccessControl": [
        "cloudfront:CreateOriginAccessControl",
        "cloudfront:UpdateOriginAccessControl",
        "cloudfront:DeleteOriginAccessControl",
        "cloudfront:GetOriginAccessControl",
    ],
    "AWS::CloudFront::ResponseHeadersPolicy": [
        "cloudfront:CreateResponseHeadersPolicy",
        "cloudfront:UpdateResponseHeadersPolicy",
        "cloudfront:DeleteResponseHeadersPolicy",
        "cloudfront:GetResponseHeadersPolicy",
    ],
    "AWS::CloudWatch::Alarm": [
        "cloudwatch:PutMetricAlarm",
        "cloudwatch:DeleteAlarms",
        "cloudwatch:DescribeAlarms",
    ],
    "AWS::CloudWatch::Dashboard": [
        "cloudwatch:PutDashboard",
        "cloudwatch:DeleteDashboards",
        "cloudwatch:GetDashboard",
    ],
    "AWS::DynamoDB::Table": [
        "dynamodb:CreateTable",
        "dynamodb:UpdateTable",
        "dynamodb:DeleteTable",
        "dynamodb:DescribeTable",
    ],
    "AWS::IAM::Role": [
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:GetRole",
        "iam:UpdateAssumeRolePolicy",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PassRole",
        "iam:TagRole",
    ],
    "AWS::Lambda::EventSourceMapping": [
        "lambda:CreateEventSourceMapping",
        "lambda:UpdateEventSourceMapping",
        "lambda:DeleteEventSourceMapping",
        "lambda:GetEventSourceMapping",
    ],
    "AWS::Lambda::Function": [
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:DeleteFunction",
        "lambda:GetFunction",
        "lambda:TagResource",
    ],
    "AWS::Lambda::Permission": [
        "lambda:AddPermission",
        "lambda:RemovePermission",
        "lambda:GetPolicy",
    ],
    "AWS::Logs::LogGroup": [
        "logs:CreateLogGroup",
        "logs:DeleteLogGroup",
        "logs:PutRetentionPolicy",
    ],
    "AWS::S3::Bucket": [
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:PutBucketTagging",
        "s3:TagResource",
        "s3:ListTagsForResource",
        "s3:PutLifecycleConfiguration",
        "s3:PutEncryptionConfiguration",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutBucketNotification",
        "s3:PutBucketCORS",
    ],
    "AWS::S3::BucketPolicy": ["s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:GetBucketPolicy"],
    "AWS::SNS::Subscription": ["sns:Subscribe", "sns:Unsubscribe", "sns:GetSubscriptionAttributes"],
    "AWS::SNS::Topic": [
        "sns:CreateTopic",
        "sns:DeleteTopic",
        "sns:SetTopicAttributes",
        "sns:GetTopicAttributes",
        "sns:TagResource",
    ],
    "AWS::SNS::TopicPolicy": ["sns:SetTopicAttributes"],
    "AWS::SQS::Queue": [
        "sqs:CreateQueue",
        "sqs:DeleteQueue",
        "sqs:SetQueueAttributes",
        "sqs:GetQueueAttributes",
        "sqs:TagQueue",
    ],
    "AWS::SQS::QueuePolicy": ["sqs:SetQueueAttributes"],
    "AWS::StepFunctions::StateMachine": [
        "states:CreateStateMachine",
        "states:UpdateStateMachine",
        "states:DeleteStateMachine",
        "states:DescribeStateMachine",
        "states:TagResource",
    ],
}


def test_every_resource_type_the_stack_uses_has_reviewed_permissions() -> None:
    """Add a resource type to template.yaml and this fails until its permissions are reviewed,
    instead of the first CI deploy failing with AccessDenied."""
    used = {r["Type"] for r in expanded_template()["Resources"].values()}
    assert used == set(REQUIRED_FOR_TYPE), sorted(used ^ set(REQUIRED_FOR_TYPE))


@pytest.mark.parametrize("resource_type", sorted(REQUIRED_FOR_TYPE))
def test_the_execution_role_can_create_change_and_delete_each_resource_type(
    resource_type: str,
) -> None:
    lacking = [a for a in REQUIRED_FOR_TYPE[resource_type] if not permits(EXEC_ALLOWED, a)]
    assert lacking == []


# --- outputs and the workflow's use of them -------------------------------------------------------


def test_the_outputs_give_the_two_role_arns() -> None:
    assert TEMPLATE["Outputs"]["DeployRoleArn"]["Value"] == {"GetAtt": "DeployRole.Arn"}
    assert TEMPLATE["Outputs"]["ExecutionRoleArn"]["Value"] == {"GetAtt": "ExecutionRole.Arn"}


def test_the_default_branch_is_the_one_the_deploy_job_will_run_on() -> None:
    assert PARAMETERS["GitHubBranch"]["Default"] == "main"
