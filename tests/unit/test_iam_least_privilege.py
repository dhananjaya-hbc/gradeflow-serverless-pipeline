"""The permissions of every role, checked on the template as SAM expands it.

The review in one sentence: each Lambda may do exactly what its step does, on exactly the keys it
touches, and nothing else. These tests pin that, so a permission can only change on purpose.
"""

from typing import Any

import pytest
from tests.unit.sam_expand import (
    BASIC_EXECUTION_POLICY,
    SQS_EXECUTION_POLICY,
    expanded_template,
    listed,
    permissions,
    readable,
    roles,
)
from tests.unit.test_template import FUNCTIONS, RESOURCES

RAW = "${RawBucket.Arn}"
PROCESSED = "${ProcessedBucket.Arn}"
QUARANTINE = "${QuarantineBucket.Arn}"
TABLE = "PipelineTable.Arn"
STATE_MACHINE = (
    "arn:${AWS::Partition}:states:${AWS::Region}:${AWS::AccountId}"
    ":stateMachine:PipelineStateMachine.Name"
)

# What each function may do, as (action, resource). Everything not listed is denied.
EXPECTED: dict[str, set[tuple[str, str]]] = {
    "TriggerFunction": {
        ("s3:GetObject", f"{RAW}/uploads/*"),  # fingerprint the upload
        ("s3:ListBucket", "RawBucket.Arn"),  # a missing file is "not found", not "access denied"
        ("dynamodb:PutItem", TABLE),
        ("dynamodb:GetItem", TABLE),
        ("states:StartExecution", STATE_MACHINE),
        ("sqs:ReceiveMessage", "UploadQueue.Arn"),  # this one queue, not every queue
        ("sqs:DeleteMessage", "UploadQueue.Arn"),
        ("sqs:GetQueueAttributes", "UploadQueue.Arn"),
    },
    "ValidateFunction": {
        ("s3:GetObject", f"{RAW}/uploads/*"),
        ("s3:ListBucket", "RawBucket.Arn"),
        ("s3:PutObject", f"{PROCESSED}/work/*"),
    },
    "CleanFunction": {
        ("s3:GetObject", f"{PROCESSED}/work/*"),
        ("s3:PutObject", f"{PROCESSED}/processed/*"),
    },
    "StatsFunction": {
        ("s3:GetObject", f"{PROCESSED}/processed/*"),  # read-only
    },
    "AnomaliesFunction": {
        ("s3:GetObject", f"{PROCESSED}/processed/*"),
        ("s3:PutObject", f"{PROCESSED}/work/*"),
    },
    "StoreFunction": {
        ("dynamodb:PutItem", TABLE),
        ("dynamodb:Query", TABLE),
        ("dynamodb:DeleteItem", TABLE),
        ("dynamodb:BatchWriteItem", TABLE),
        ("s3:GetObject", f"{PROCESSED}/work/*"),
    },
    "FailFunction": {
        ("s3:GetObject", f"{RAW}/uploads/*"),  # the quarantine copy reads the original
        ("s3:PutObject", f"{QUARANTINE}/quarantine/*"),
        ("dynamodb:PutItem", TABLE),
    },
    "ApiFunction": {
        ("dynamodb:GetItem", TABLE),
        ("dynamodb:GetItem", "${PipelineTable.Arn}/index/GSI1"),
        ("dynamodb:Query", TABLE),
        ("dynamodb:Query", "${PipelineTable.Arn}/index/GSI1"),
        ("s3:PutObject", f"{RAW}/uploads/*"),  # only to sign upload forms
    },
}

ROLES = roles()


def role_of(function: str) -> dict[str, Any]:
    return ROLES[f"{function}Role"]


def test_the_table_covers_every_function() -> None:
    assert set(EXPECTED) == set(FUNCTIONS)


def test_every_role_belongs_to_a_function_or_the_state_machine() -> None:
    assert set(ROLES) == {f"{name}Role" for name in FUNCTIONS} | {"PipelineStateMachineRole"}


@pytest.mark.parametrize("function", sorted(EXPECTED))
def test_each_function_may_do_exactly_what_its_step_needs(function: str) -> None:
    assert permissions(role_of(function)) == EXPECTED[function]


@pytest.mark.parametrize("function", sorted(EXPECTED))
def test_the_only_managed_policy_on_a_function_is_the_basic_logging_one(function: str) -> None:
    """AWSLambdaBasicExecutionRole lets it write its own logs. The SQS one (every queue in the
    account) is what SAM adds for `Events: SQS`, and it must not come back."""
    assert role_of(function)["ManagedPolicyArns"] == [BASIC_EXECUTION_POLICY]
    assert SQS_EXECUTION_POLICY not in str(ROLES)


def test_the_queue_mapping_keeps_the_logical_id_sam_generated() -> None:
    """A different logical ID would make CloudFormation create a second mapping before it deletes
    the old one, and Lambda refuses two mappings for one function and queue: the deploy would fail.
    `TriggerFunctionUploadedFile` is the ID SAM generated for the old `Events: UploadedFile`."""
    mappings = {
        name: resource["Properties"]
        for name, resource in RESOURCES.items()
        if resource["Type"] == "AWS::Lambda::EventSourceMapping"
    }
    assert mappings == {
        "TriggerFunctionUploadedFile": {
            "FunctionName": {"Ref": "TriggerFunction"},
            "EventSourceArn": {"GetAtt": "UploadQueue.Arn"},
            "BatchSize": 1,
        }
    }


def test_no_function_has_an_sqs_event_so_sam_cannot_add_the_broad_policy_again() -> None:
    for name, properties in FUNCTIONS.items():
        kinds = {event["Type"] for event in properties.get("Events", {}).values()}
        assert "SQS" not in kinds, name


# --- limits that hold for every role ----------------------------------------------------------

FORBIDDEN_ACTIONS = [
    "s3:PutLifecycleConfiguration",  # would let a function change when files are deleted
    "s3:PutObjectAcl",
    "s3:GetBucketLocation",
    "s3:GetObjectVersion",
    "s3:GetLifecycleConfiguration",
    "s3:DeleteObject",  # no function deletes anything from S3
    "s3:*",
    "dynamodb:*",
    "dynamodb:Scan",
    "dynamodb:UpdateItem",
    "sqs:*",
]


@pytest.mark.parametrize("action", FORBIDDEN_ACTIONS)
def test_no_role_has_an_action_nobody_needs(action: str) -> None:
    for name, role in ROLES.items():
        assert action not in {a for a, _ in permissions(role)}, f"{name} has {action}"


def test_no_role_allows_a_wildcard_action_or_resource() -> None:
    for name, role in ROLES.items():
        for action, resource in permissions(role):
            assert "*" not in action, f"{name}: {action}"
            assert resource != "*", f"{name}: {action} on every resource"


def test_every_s3_write_is_limited_to_one_prefix_never_a_whole_bucket() -> None:
    writes = {
        (name, resource)
        for name, role in ROLES.items()
        for action, resource in permissions(role)
        if action == "s3:PutObject"
    }
    assert writes  # the check below must have something to check
    for name, resource in writes:
        assert resource.endswith(("/work/*", "/processed/*", "/quarantine/*", "/uploads/*")), (
            name,
            resource,
        )


def test_the_raw_bucket_is_written_only_through_signed_forms() -> None:
    """The pipeline never writes to the raw bucket; only the API's signer can (for the uploader)."""
    writers = {
        name
        for name, role in ROLES.items()
        for action, resource in permissions(role)
        if action == "s3:PutObject" and resource.startswith(RAW)
    }
    assert writers == {"ApiFunctionRole"}


def test_a_function_that_only_reads_cannot_write() -> None:
    writes = {a for a, _ in permissions(role_of("StatsFunction"))} & {
        "s3:PutObject",
        "dynamodb:PutItem",
        "dynamodb:DeleteItem",
    }
    assert writes == set()


# --- the state machine's role ---------------------------------------------------------------------

STEP_FUNCTIONS = {
    "ValidateFunction",
    "CleanFunction",
    "StatsFunction",
    "AnomaliesFunction",
    "StoreFunction",
    "FailFunction",
}


def test_the_state_machine_may_invoke_only_the_six_steps_by_exact_arn() -> None:
    granted = permissions(ROLES["PipelineStateMachineRole"])
    assert granted == {
        *{("lambda:InvokeFunction", f"{name}.Arn") for name in STEP_FUNCTIONS},
        ("sns:Publish", "AlertTopic"),  # the failure alert, to this one topic
    }
    # not the Trigger or the API: they are not workflow steps
    assert ("lambda:InvokeFunction", "TriggerFunction.Arn") not in granted
    assert ("lambda:InvokeFunction", "ApiFunction.Arn") not in granted


def test_no_resource_ends_in_a_wildcard_except_s3_key_prefixes() -> None:
    """SAM's own policy templates add a trailing * to function ARNs (a name prefix). Not here."""
    for name, role in ROLES.items():
        for action, resource in permissions(role):
            if resource.endswith("*"):
                assert action.startswith("s3:"), (name, action, resource)
                assert resource.endswith(("/work/*", "/processed/*", "/quarantine/*", "/uploads/*"))


# --- buckets only talk over HTTPS ---------------------------------------------------------------


def bucket_policies() -> dict[str, dict[str, Any]]:
    """bucket logical id -> its policy document, from the expanded template."""
    found = {}
    for resource in expanded_template()["Resources"].values():
        if resource["Type"] == "AWS::S3::BucketPolicy":
            found[readable(resource["Properties"]["Bucket"])] = resource["Properties"][
                "PolicyDocument"
            ]
    return found


BUCKETS = sorted(n for n, r in RESOURCES.items() if r["Type"] == "AWS::S3::Bucket")


@pytest.mark.parametrize("bucket", BUCKETS)
def test_every_bucket_refuses_plain_http(bucket: str) -> None:
    statements = [
        s
        for s in bucket_policies().get(bucket, {"Statement": []})["Statement"]
        if s["Effect"] == "Deny"
    ]
    assert len(statements) == 1, f"{bucket} has no (single) deny-without-TLS statement"
    statement = statements[0]
    assert statement["Principal"] == "*"
    assert statement["Action"] == "s3:*"
    assert statement["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}
    resources = {readable(r) for r in listed(statement["Resource"])}
    assert resources == {f"{bucket}.Arn", f"${{{bucket}.Arn}}/*"}  # the bucket and its objects


def test_only_the_dashboard_bucket_has_an_allow_statement_in_its_policy() -> None:
    """The other buckets' policies are deny-only: access comes from roles, never from the bucket."""
    for bucket, document in bucket_policies().items():
        allows = [s for s in document["Statement"] if s["Effect"] == "Allow"]
        assert bool(allows) == (bucket == "DashboardBucket"), bucket
