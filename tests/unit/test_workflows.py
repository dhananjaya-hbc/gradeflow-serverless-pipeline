"""Checks .github/workflows/*.yml: what CI may do, and that it cannot do more by accident.

A workflow runs on GitHub, where a mistake costs a failed push (or, worse, an exposed secret or an
over-powerful deploy). These tests read the files like the template tests read template.yaml.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CI_PATH = WORKFLOW_DIR / "ci.yml"
CI_TEXT = CI_PATH.read_text()
CI: dict[str, Any] = yaml.safe_load(CI_TEXT)
JOBS: dict[str, dict[str, Any]] = CI["jobs"]
TRIGGERS: dict[str, Any] = CI[True]  # YAML 1.1 reads the key `on` as the boolean True

ALL_WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))


def steps(job: str) -> list[dict[str, Any]]:
    return JOBS[job]["steps"]


def commands(job: str) -> list[str]:
    return [step["run"] for step in steps(job) if "run" in step]


# --- the files are valid GitHub Actions workflows -----------------------------------------------


@pytest.mark.parametrize("path", ALL_WORKFLOWS, ids=lambda p: p.name)
def test_actionlint_finds_nothing_wrong(path: Path) -> None:
    """actionlint knows the real workflow syntax, expressions and action inputs."""
    binary = Path(sys.executable).parent / "actionlint"
    assert binary.exists(), "actionlint is not installed: pip install -r requirements-dev.txt"
    result = subprocess.run(
        [str(binary), "-color=false", str(path)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- when it runs --------------------------------------------------------------------------------


def test_it_runs_on_every_push_and_on_pull_requests_to_main() -> None:
    assert TRIGGERS["push"] == {"branches": ["**"]}  # branches only: a tag push does not redeploy
    assert TRIGGERS["pull_request"] == {"branches": ["main"]}


def test_it_never_runs_on_triggers_that_give_untrusted_code_secrets_or_write_access() -> None:
    """pull_request_target and workflow_run give a fork's code the repository's privileges."""
    assert set(TRIGGERS) <= {"push", "pull_request", "workflow_dispatch"}
    for forbidden in ("pull_request_target", "workflow_run", "issue_comment"):
        assert forbidden not in CI_TEXT.split("jobs:")[0]


def test_a_newer_push_replaces_a_running_check_except_on_main() -> None:
    concurrency = CI["concurrency"]
    assert concurrency["group"] == "ci-${{ github.ref }}"
    assert concurrency["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}"


# --- least privilege ------------------------------------------------------------------------------


def test_the_workflow_can_only_read_the_repository_by_default() -> None:
    assert CI["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("job", sorted(JOBS))
def test_a_check_job_gets_no_extra_permissions(job: str) -> None:
    """Jobs that add `id-token: write` (to assume the AWS role) are the deploy jobs only."""
    permissions = JOBS[job].get("permissions", {})
    if job.startswith("deploy"):
        return
    assert permissions == {}, f"{job} asks for {permissions}"


@pytest.mark.parametrize("job", sorted(JOBS))
def test_every_job_has_a_timeout(job: str) -> None:
    assert 0 < JOBS[job]["timeout-minutes"] <= 60


@pytest.mark.parametrize("job", sorted(JOBS))
def test_every_job_runs_on_a_pinned_runner_image(job: str) -> None:
    """`ubuntu-latest` changes under you; a numbered image changes when you decide."""
    assert re.fullmatch(r"ubuntu-\d+\.\d+(-arm)?", JOBS[job]["runs-on"]), JOBS[job]["runs-on"]


def test_no_secret_and_no_long_lived_aws_key_is_used_anywhere() -> None:
    """Deploys use OIDC (a short-lived role), never stored keys."""
    for needle in (
        "aws-access-key-id",
        "aws-secret-access-key",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET",
        "AWS_SESSION_TOKEN",
    ):
        assert needle not in CI_TEXT, needle
    assert not re.search(r"\b(AKIA|ASIA)[0-9A-Z]{8,}", CI_TEXT)  # the shape of an access key id


@pytest.mark.parametrize("job", sorted(JOBS))
def test_checkout_does_not_leave_the_token_in_the_git_config(job: str) -> None:
    for step in steps(job):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False


def test_every_action_is_pinned_to_a_full_commit_sha_with_its_version_in_a_comment() -> None:
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", CI_TEXT, flags=re.MULTILINE)
    assert uses  # the check below must have something to check
    for reference, rest in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", reference), (
            f"{reference} is not pinned to a commit SHA"
        )
        assert re.search(r"#\s*v\d", rest), f"{reference} has no '# vX' version comment"


# --- what the check jobs run ----------------------------------------------------------------------


def test_the_python_job_lints_checks_formatting_and_runs_all_tests() -> None:
    assert commands("python") == [
        "pip install -r requirements-dev.txt",
        "ruff check .",
        "ruff format --check .",
        "pytest",
    ]


def test_the_python_job_uses_the_lambda_runtime_python() -> None:
    (setup,) = [s for s in steps("python") if s.get("uses", "").startswith("actions/setup-python@")]
    assert setup["with"]["python-version"] == "3.12"
    assert setup["with"]["cache"] == "pip"
    assert setup["with"]["cache-dependency-path"] == "requirements-dev.txt"


def test_the_dashboard_job_installs_from_the_lockfile_then_checks_tests_and_builds() -> None:
    assert commands("dashboard") == [
        "npm ci",  # exactly the locked versions; `npm install` could change them
        "npm run typecheck",
        "npm test",
        "npm run build",
    ]
    assert JOBS["dashboard"]["defaults"]["run"]["working-directory"] == "dashboard"
    (setup,) = [
        s for s in steps("dashboard") if s.get("uses", "").startswith("actions/setup-node@")
    ]
    assert setup["with"]["cache-dependency-path"] == "dashboard/package-lock.json"
    assert (ROOT / "dashboard" / "package-lock.json").exists()


def test_the_template_job_validates_and_lints_with_a_pinned_sam_cli() -> None:
    assert commands("template") == ["sam validate --lint --region ap-south-1"]
    (setup,) = [
        s for s in steps("template") if s.get("uses", "").startswith("aws-actions/setup-sam@")
    ]
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(setup["with"]["version"]))  # an exact version
    assert CI["env"]["SAM_CLI_TELEMETRY"] == "0"


def test_the_template_job_matches_the_region_the_project_deploys_to() -> None:
    samconfig = (ROOT / "samconfig.toml").read_text()
    assert 'region = "ap-south-1"' in samconfig


def test_the_ci_python_and_the_function_runtime_agree() -> None:
    template = (ROOT / "template.yaml").read_text()
    assert "Runtime: python3.12" in template
    requires = re.search(r'requires-python = ">=(\d+\.\d+)"', (ROOT / "pyproject.toml").read_text())
    assert requires and requires.group(1) == "3.12"


# --- the deploy job -------------------------------------------------------------------------------

DEPLOY = JOBS["deploy"]
DEPLOY_STEPS = DEPLOY["steps"]
SETUP_DOC = (ROOT / "docs" / "ci-cd-setup.md").read_text()


def deploy_step(name_part: str) -> dict[str, Any]:
    (found,) = [s for s in DEPLOY_STEPS if name_part in s.get("name", s.get("uses", ""))]
    return found


def test_the_deploy_job_runs_only_for_a_push_to_main_after_every_check_passed() -> None:
    assert DEPLOY["needs"] == ["python", "dashboard", "template"]
    assert DEPLOY["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"


def test_the_deploy_job_builds_the_arm_image_on_an_arm_runner() -> None:
    assert DEPLOY["runs-on"] == "ubuntu-24.04-arm"
    assert DEPLOY["timeout-minutes"] <= 60


def test_only_the_deploy_job_can_ask_github_for_an_identity_token() -> None:
    assert DEPLOY["permissions"] == {"contents": "read", "id-token": "write"}
    others = [name for name, job in JOBS.items() if name != "deploy" and "permissions" in job]
    assert others == []
    assert "id-token" not in str(CI["permissions"])


def test_deploys_never_overlap_and_a_running_deploy_is_never_cancelled() -> None:
    assert DEPLOY["concurrency"] == {"group": "deploy-aws", "cancel-in-progress": False}


def test_the_aws_login_uses_oidc_with_the_deploy_role_and_nothing_long_lived() -> None:
    login = deploy_step("aws-actions/configure-aws-credentials")
    assert login["with"] == {
        "role-to-assume": "${{ secrets.AWS_DEPLOY_ROLE_ARN }}",
        "aws-region": "${{ vars.AWS_REGION }}",
        "role-session-name": "github-deploy-${{ github.run_id }}",
        "mask-aws-account-id": True,
    }


def test_the_steps_run_in_the_order_that_makes_a_deploy_safe() -> None:
    labels = [s.get("name", s.get("uses", "")).split("@")[0] for s in DEPLOY_STEPS]
    order = [
        "Check the deploy variables are set",
        "actions/checkout",
        "aws-actions/configure-aws-credentials",
        "Build and deploy the stack",
        "Publish the dashboard",
        "Smoke test the deployed site",
    ]
    positions = [labels.index(step) for step in order]
    assert positions == sorted(positions)
    assert labels[0] == "Check the deploy variables are set"  # before anything else can fail oddly
    assert labels[-1] == "Smoke test the deployed site"  # a broken site turns the run red


def test_the_deploy_runs_the_tested_scripts_not_inline_commands() -> None:
    assert deploy_step("Build and deploy the stack")["run"] == "scripts/ci_deploy.sh"
    assert deploy_step("Publish the dashboard")["run"] == "scripts/deploy_dashboard.sh"
    smoke = deploy_step("Smoke test the deployed site")["run"]
    assert 'python3 scripts/smoke_test.py --dashboard-url "$DASHBOARD_URL"' in smoke
    for script in ("ci_deploy.sh", "deploy_dashboard.sh", "smoke_test.py"):
        assert (ROOT / "scripts" / script).exists()
    for script in ("ci_deploy.sh", "deploy_dashboard.sh"):
        assert (ROOT / "scripts" / script).stat().st_mode & 0o111, f"{script} is not executable"


def test_the_smoke_test_asks_the_stack_for_the_dashboard_address_it_just_deployed() -> None:
    smoke = deploy_step("Smoke test the deployed site")["run"]
    assert "describe-stacks" in smoke and "DashboardUrl" in smoke
    assert "$STACK_NAME" in smoke and "$AWS_REGION" in smoke


def test_the_deploy_job_uses_the_same_sam_version_as_the_template_job() -> None:
    def version(job: str) -> str:
        (setup,) = [s for s in steps(job) if s.get("uses", "").startswith("aws-actions/setup-sam@")]
        return str(setup["with"]["version"])

    assert version("deploy") == version("template")


def test_the_deploy_stack_name_is_the_one_the_project_deploys() -> None:
    assert DEPLOY["env"]["STACK_NAME"] == "gradeflow"
    assert 'stack_name = "gradeflow"' in (ROOT / "samconfig.toml").read_text()


def test_no_step_may_fail_and_let_the_job_carry_on() -> None:
    assert "continue-on-error" not in CI_TEXT


def test_only_the_deploy_job_touches_aws_variables_or_credentials() -> None:
    for name in ("python", "dashboard", "template"):
        text = yaml.safe_dump(JOBS[name])
        assert "vars." not in text and "secrets." not in text, name
        assert "configure-aws-credentials" not in text, name
        assert "id-token" not in text and "AWS_" not in text, name


# --- the secrets and variables the workflow reads, and the guide that tells you to set them -----

# Secrets are for values that would otherwise be printed in this public repository's run logs:
# GitHub prints a variable's value (in a step's env: and with: blocks) and masks only secrets.
# None of these is a credential; they hold the AWS account id or a personal address.
ALLOWED_SECRETS = {"AWS_DEPLOY_ROLE_ARN", "AWS_CFN_ROLE_ARN", "ALERT_EMAIL"}
ALLOWED_VARIABLES = {"AWS_REGION", "API_ALLOWED_ORIGIN", "DASHBOARD_DEV_ORIGIN"}


def workflow_variables() -> set[str]:
    return set(re.findall(r"vars\.([A-Z][A-Z0-9_]*)", CI_TEXT))


def workflow_secrets() -> set[str]:
    return set(re.findall(r"secrets\.([A-Z][A-Z0-9_]*)", CI_TEXT))


def test_the_workflow_reads_exactly_these_secrets_and_these_variables() -> None:
    assert workflow_secrets() == ALLOWED_SECRETS
    assert workflow_variables() == ALLOWED_VARIABLES


def test_nothing_that_holds_an_account_id_or_an_address_is_a_variable() -> None:
    """A variable is printed in the public log. These names carry the account id or an email."""
    for name in workflow_variables():
        assert not re.search(r"ARN|EMAIL|ACCOUNT|MAIL", name), f"{name} would be printed in the log"


def test_no_secret_is_named_like_a_credential() -> None:
    """Deploys use OIDC. A secret called a key, token or password would mean stored credentials."""
    for name in workflow_secrets():
        assert not re.search(r"KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL", name), name


def test_every_secret_and_variable_the_workflow_reads_is_explained_in_the_setup_guide() -> None:
    documented_secrets = set(re.findall(r"gh secret set ([A-Z][A-Z0-9_]*)", SETUP_DOC))
    documented_variables = set(re.findall(r"gh variable set ([A-Z][A-Z0-9_]*)", SETUP_DOC))
    assert workflow_secrets() == documented_secrets, workflow_secrets() ^ documented_secrets
    assert workflow_variables() == documented_variables, workflow_variables() ^ documented_variables


def test_the_guide_explains_why_these_are_secrets() -> None:
    assert "prints a *variable's* value in the run log" in SETUP_DOC
    assert "gh secret set AWS_DEPLOY_ROLE_ARN" in SETUP_DOC


def test_the_deploy_job_passes_the_secrets_through_env_so_they_are_masked_in_the_log() -> None:
    env = DEPLOY["env"]
    assert env["AWS_CFN_ROLE_ARN"] == "${{ secrets.AWS_CFN_ROLE_ARN }}"
    assert env["ALERT_EMAIL"] == "${{ secrets.ALERT_EMAIL }}"
    assert deploy_step("Check the deploy variables are set")["env"] == {
        "DEPLOY_ROLE": "${{ secrets.AWS_DEPLOY_ROLE_ARN }}"
    }


def run_check(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = deploy_step("Check the deploy variables are set")["run"]
    bash = "/bin/bash"
    return subprocess.run(
        [bash, "-c", script], env={"PATH": "/usr/bin:/bin", **env}, capture_output=True, text=True
    )


ALL_REQUIRED = {"DEPLOY_ROLE": "arn", "AWS_CFN_ROLE_ARN": "arn", "AWS_REGION": "ap-south-1"}


def test_the_variable_check_passes_when_everything_is_set() -> None:
    assert run_check(ALL_REQUIRED).returncode == 0


@pytest.mark.parametrize(
    ("missing", "named"),
    [
        ("DEPLOY_ROLE", "AWS_DEPLOY_ROLE_ARN"),
        ("AWS_CFN_ROLE_ARN", "AWS_CFN_ROLE_ARN"),
        ("AWS_REGION", "AWS_REGION"),
    ],
)
def test_the_variable_check_names_the_variable_that_is_missing(missing: str, named: str) -> None:
    env = {k: v for k, v in ALL_REQUIRED.items() if k != missing}
    result = run_check(env)
    assert result.returncode != 0
    assert named in result.stderr
    assert "docs/ci-cd-setup.md" in result.stderr


def test_an_empty_variable_counts_as_missing() -> None:
    assert run_check({**ALL_REQUIRED, "AWS_REGION": ""}).returncode != 0
