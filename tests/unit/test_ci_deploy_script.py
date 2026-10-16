"""scripts/ci_deploy.sh with a fake `sam` that records exactly what it was given.

The script builds the arguments of `sam deploy`, and two details decide whether a CI deploy works:
every parameter must be passed (SAM forgets them between deploys, so leaving one out silently
resets it to the template default), and an empty value must be quoted (SAM rejects a bare
`AlertEmail=`). Where the SAM CLI is installed, the tests also give the recorded arguments to SAM's
own parser.
"""

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from tests.unit.test_template import TEMPLATE

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci_deploy.sh"
BASH = shutil.which("bash") or "/bin/bash"
ROLE = "arn:aws:iam::" + "999988887777" + ":role/ci-gradeflow-cfn-exec"  # a made-up account

FAKE_SAM = r"""#!/usr/bin/env bash
{ echo "--- sam"; printf '%s\n' "$@"; } >> "$FAKE_LOG"
[ "${FAKE_SAM_FAILS:-}" = "$1" ] && exit 3
exit 0
"""


@pytest.fixture
def sandbox(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sam = bin_dir / "sam"
    sam.write_text(FAKE_SAM)
    sam.chmod(sam.stat().st_mode | stat.S_IXUSR)
    return {"bin": bin_dir, "log": tmp_path / "calls.log"}


def run_script(sandbox: dict, **env: str) -> subprocess.CompletedProcess[str]:
    full = {
        "PATH": f"{sandbox['bin']}:{os.environ['PATH']}",
        "FAKE_LOG": str(sandbox["log"]),
        "AWS_CFN_ROLE_ARN": ROLE,
        **env,
    }
    full = {k: v for k, v in full.items() if v is not None}
    return subprocess.run([BASH, str(SCRIPT)], env=full, capture_output=True, text=True, timeout=60)


def calls(sandbox: dict) -> list[list[str]]:
    """The recorded `sam` invocations, each as its list of arguments."""
    if not sandbox["log"].exists():
        return []
    invocations: list[list[str]] = []
    for line in sandbox["log"].read_text().splitlines():
        if line == "--- sam":
            invocations.append([])
        else:
            invocations[-1].append(line)
    return invocations


def deploy_args(sandbox: dict) -> list[str]:
    (build, deploy) = calls(sandbox)
    assert build == ["build"]
    assert deploy[0] == "deploy"
    return deploy


def option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def overrides(args: list[str]) -> list[str]:
    return args[args.index("--parameter-overrides") + 1 :]


# --- the normal run -------------------------------------------------------------------------------


def test_it_builds_and_then_deploys_with_the_roles_flags_and_every_parameter(sandbox) -> None:
    result = run_script(sandbox, ALERT_EMAIL="me@example.org")
    assert result.returncode == 0, result.stderr
    args = deploy_args(sandbox)
    assert option(args, "--stack-name") == "gradeflow"
    assert option(args, "--region") == "ap-south-1"
    assert option(args, "--role-arn") == ROLE  # CloudFormation builds the stack with this role
    assert "--no-confirm-changeset" in args  # CI cannot answer a prompt
    assert "--no-fail-on-empty-changeset" in args  # a push that changes nothing is not a failure
    assert overrides(args) == [
        'AlertEmail="me@example.org"',
        'AllowedOrigin="*"',
        'DashboardDevOrigin="http://localhost:5173"',
    ]


def test_an_empty_alert_email_is_passed_quoted_because_sam_rejects_a_bare_empty_value(
    sandbox,
) -> None:
    run_script(sandbox)  # ALERT_EMAIL not set
    assert 'AlertEmail=""' in overrides(deploy_args(sandbox))
    assert "AlertEmail=" not in overrides(deploy_args(sandbox))  # never the bare form


def test_the_dev_origin_can_be_turned_off_with_none(sandbox) -> None:
    run_script(sandbox, DASHBOARD_DEV_ORIGIN="none")
    assert 'DashboardDevOrigin=""' in overrides(deploy_args(sandbox))


def test_settings_from_the_environment_override_the_defaults(sandbox) -> None:
    run_script(
        sandbox,
        STACK_NAME="other-stack",
        AWS_REGION="eu-west-1",
        API_ALLOWED_ORIGIN="https://dash.example.com",
        DASHBOARD_DEV_ORIGIN="http://localhost:3000",
    )
    args = deploy_args(sandbox)
    assert option(args, "--stack-name") == "other-stack"
    assert option(args, "--region") == "eu-west-1"
    assert 'AllowedOrigin="https://dash.example.com"' in overrides(args)
    assert 'DashboardDevOrigin="http://localhost:3000"' in overrides(args)


def test_it_never_prints_the_full_alert_email_address(sandbox) -> None:
    result = run_script(sandbox, ALERT_EMAIL="someone.private@example.org")
    assert "someone.private" not in result.stdout + result.stderr
    assert "***@example.org" in result.stdout


def test_it_never_asks_sam_to_prompt_or_guess(sandbox) -> None:
    run_script(sandbox)
    args = deploy_args(sandbox)
    for forbidden in ("--guided", "--confirm-changeset", "--resolve-s3", "--force-upload"):
        assert forbidden not in args


# --- refuses bad input before touching AWS --------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"AWS_CFN_ROLE_ARN": None}, "AWS_CFN_ROLE_ARN is not set"),
        ({"AWS_CFN_ROLE_ARN": "not-an-arn"}, "not an IAM role ARN"),
        ({"AWS_CFN_ROLE_ARN": "arn:aws:iam::123:role/x"}, "not an IAM role ARN"),
        ({"AWS_CFN_ROLE_ARN": "arn:aws:s3:::bucket"}, "not an IAM role ARN"),
        ({"STACK_NAME": "Bad Name"}, "not a valid stack name"),
        ({"AWS_REGION": "mars"}, "not a region name"),
        ({"ALERT_EMAIL": "no-at-sign"}, "ALERT_EMAIL is not an email address"),
        ({"ALERT_EMAIL": "a b@example.org"}, "contains a space"),
        ({"ALERT_EMAIL": 'a"b@example.org'}, "contains a space, a quote"),
        ({"ALERT_EMAIL": "a'b@example.org"}, "contains a space, a quote"),
        ({"ALERT_EMAIL": "a\\b@example.org"}, "contains a space, a quote"),
        ({"ALERT_EMAIL": "a$b@example.org"}, "contains a space, a quote"),
        ({"API_ALLOWED_ORIGIN": "example.com"}, "must be * or an origin"),
        ({"API_ALLOWED_ORIGIN": "https://example.com/path"}, "must be * or an origin"),
        ({"API_ALLOWED_ORIGIN": "https://*.example.com"}, "must be * or an origin"),
        ({"API_ALLOWED_ORIGIN": 'https://a.com" --evil'}, "contains a space, a quote"),
        ({"DASHBOARD_DEV_ORIGIN": "localhost:5173"}, "must be an origin"),
        ({"DASHBOARD_DEV_ORIGIN": "http://localhost:5173/"}, "must be an origin"),
        ({"DASHBOARD_DEV_ORIGIN": "*"}, "must be an origin"),
    ],
)
def test_a_bad_value_is_refused_and_sam_is_never_run(
    sandbox, env: dict[str, str | None], message: str
) -> None:
    result = run_script(sandbox, **env)  # type: ignore[arg-type]
    assert result.returncode == 1, result.stdout
    assert message in result.stderr
    assert calls(sandbox) == []  # nothing was built, nothing was deployed


# --- failures -------------------------------------------------------------------------------------


def test_a_failed_build_stops_before_the_deploy(sandbox) -> None:
    result = run_script(sandbox, FAKE_SAM_FAILS="build")
    assert result.returncode == 3
    assert [c[0] for c in calls(sandbox)] == ["build"]


def test_a_failed_deploy_fails_the_script_so_the_job_turns_red(sandbox) -> None:
    result = run_script(sandbox, FAKE_SAM_FAILS="deploy")
    assert result.returncode == 3
    assert [c[0] for c in calls(sandbox)] == ["build", "deploy"]


# --- against SAM itself and the template ----------------------------------------------------------


def sam_python() -> str | None:
    sam = shutil.which("sam")
    if not sam:
        return None
    first = Path(sam).resolve().read_text(errors="ignore").splitlines()[0]
    return first[2:] if first.startswith("#!") and Path(first[2:]).exists() else None


@pytest.mark.parametrize(
    "env",
    [
        {"ALERT_EMAIL": "me@example.org"},
        {},  # empty email
        {"DASHBOARD_DEV_ORIGIN": "none"},  # empty dev origin
        {"API_ALLOWED_ORIGIN": "https://dash.example.com"},
    ],
)
def test_sams_own_parser_accepts_the_arguments_and_reads_the_intended_values(
    sandbox, env: dict[str, str]
) -> None:
    python = sam_python()
    if python is None:
        pytest.skip("the SAM CLI is not installed here")
    run_script(sandbox, **env)
    code = (
        "import sys, json\n"
        "from samcli.cli.types import CfnParameterOverridesType\n"
        "args = tuple(sys.argv[1:])\n"
        "print(json.dumps(dict(CfnParameterOverridesType().convert(args, None, None))))\n"
    )
    result = subprocess.run(
        [python, "-c", code, *overrides(deploy_args(sandbox))],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed == {
        "AlertEmail": env.get("ALERT_EMAIL", ""),
        "AllowedOrigin": env.get("API_ALLOWED_ORIGIN", "*"),
        "DashboardDevOrigin": (
            "" if env.get("DASHBOARD_DEV_ORIGIN") == "none" else "http://localhost:5173"
        ),
    }


def test_every_override_names_a_real_template_parameter_with_a_compatible_default(sandbox) -> None:
    run_script(sandbox)
    names = [re.match(r"(\w+)=", o).group(1) for o in overrides(deploy_args(sandbox))]
    parameters = TEMPLATE["Parameters"]
    assert set(names) <= set(parameters), set(names) - set(parameters)
    # The values CI passes by default are the template's own defaults for these three.
    assert parameters["AllowedOrigin"]["Default"] == "*"
    assert parameters["DashboardDevOrigin"]["Default"] == "http://localhost:5173"
    assert parameters["AlertEmail"]["Default"] == ""


def test_the_script_is_executable_and_never_traces_commands() -> None:
    assert os.access(SCRIPT, os.X_OK)
    text = SCRIPT.read_text()
    assert "set -x" not in text and "xtrace" not in text  # tracing would print values
