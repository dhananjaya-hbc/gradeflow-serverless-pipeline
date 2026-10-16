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
        "secrets.",
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
