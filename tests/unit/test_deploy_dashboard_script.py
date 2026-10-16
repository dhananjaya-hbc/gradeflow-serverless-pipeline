"""Runs scripts/deploy_dashboard.sh against a fake `aws` and a fake `npm`.

The script handles a secret (the API key), uploads to a bucket and clears a cache, and none of
that may be tried for real while testing. The fakes record every call, so the tests can check
which commands ran, in which order, and that the key never leaks to the screen.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy_dashboard.sh"
BASH = shutil.which("bash") or "/bin/bash"  # absolute: some tests run with a trimmed PATH

API_KEY = "k3yVALUEabcdefghijklmnopqrstuvwxyz0123"
OUTPUTS = {
    "ApiUrl": "https://abc123.execute-api.ap-south-1.amazonaws.com/v1/",  # note the slash
    "DashboardBucketName": "gradeflow-site-111122223333-ap-south-1",
    "DashboardDistributionId": "E1ABCDEF234567",
    "DashboardUrl": "https://d111111abcdef8.cloudfront.net",
    "ApiKeyId": "key1234567",
}

FAKE_AWS = r"""#!/usr/bin/env bash
echo "aws $*" >> "$FAKE_LOG"
case "$1 $2" in
  "cloudformation describe-stacks")
    [ -z "${FAKE_DESCRIBE_FAILS:-}" ] || { echo "An error occurred" >&2; exit 255; }
    query=""
    while [ $# -gt 0 ]; do [ "$1" = "--query" ] && query="$2"; shift; done
    key="$(echo "$query" | sed "s/.*OutputKey=='\([A-Za-z]*\)'.*/\1/")"
    value="$(grep "^$key=" "$FAKE_OUTPUTS" | head -1 | cut -d= -f2-)"
    echo "${value:-None}"
    ;;
  "apigateway get-api-key") echo "$FAKE_API_KEY" ;;
  "s3 cp")
    mkdir -p "$FAKE_UPLOADS"
    cp "$3" "$FAKE_UPLOADS/$(basename "$4")"
    ;;
  *) ;;
esac
"""

FAKE_NPM = r"""#!/usr/bin/env bash
echo "npm $*" >> "$FAKE_LOG"
case "$1 ${2:-}" in
  "test ") [ -z "${FAKE_TEST_FAILS:-}" ] || { echo "1 test failed" >&2; exit 1; } ;;
  "run build")
    [ -z "${FAKE_BUILD_FAILS:-}" ] || { echo "type error" >&2; exit 2; }
    mkdir -p dist/assets
    echo "<html></html>" > dist/index.html
    echo "console.log(1)" > dist/assets/index-abc123.js
    ;;
esac
"""


def write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def sandbox(tmp_path: Path) -> dict:
    """A fake dashboard folder, fake commands on PATH, and files to record what happened."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "aws", FAKE_AWS)
    write_executable(bin_dir / "npm", FAKE_NPM)
    dashboard = tmp_path / "dashboard"
    (dashboard / "public").mkdir(parents=True)
    (dashboard / "node_modules").mkdir()  # so the script does not run `npm ci`
    (dashboard / "package.json").write_text("{}")
    outputs = tmp_path / "outputs.txt"
    outputs.write_text("".join(f"{k}={v}\n" for k, v in OUTPUTS.items()))
    return {
        "bin": bin_dir,
        "dashboard": dashboard,
        "log": tmp_path / "calls.log",
        "uploads": tmp_path / "uploads",
        "outputs": outputs,
    }


def run_script(sandbox: dict, *args: str, extra_env: dict | None = None, path: str | None = None):
    env = {
        "PATH": path or f"{sandbox['bin']}:{os.environ['PATH']}",
        "DASHBOARD_DIR": str(sandbox["dashboard"]),
        "FAKE_LOG": str(sandbox["log"]),
        "FAKE_OUTPUTS": str(sandbox["outputs"]),
        "FAKE_UPLOADS": str(sandbox["uploads"]),
        "FAKE_API_KEY": API_KEY,
        "HOME": str(sandbox["dashboard"]),
        **(extra_env or {}),
    }
    return subprocess.run(
        [BASH, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60
    )


def calls(sandbox: dict) -> list[str]:
    log = sandbox["log"]
    return log.read_text().splitlines() if log.exists() else []


def index_of(lines: list[str], fragment: str) -> int:
    matches = [i for i, line in enumerate(lines) if fragment in line]
    assert matches, f"no call containing {fragment!r} in:\n" + "\n".join(lines)
    return matches[0]


# --- the normal run --------------------------------------------------------------------------


def test_a_normal_run_builds_uploads_and_clears_the_cache(sandbox: dict) -> None:
    result = run_script(sandbox)
    assert result.returncode == 0, result.stderr
    lines = calls(sandbox)
    bucket = OUTPUTS["DashboardBucketName"]

    assert index_of(lines, "npm test") < index_of(lines, "npm run build")  # tests gate the build
    sync = lines[index_of(lines, "aws s3 sync")]
    assert f"dist/assets s3://{bucket}/assets" in sync
    assert "--delete" in sync
    assert "max-age=31536000,immutable" in sync  # hashed files: cache for a year

    config_up = lines[index_of(lines, f"s3://{bucket}/config.json")]
    assert "--cache-control no-store" in config_up
    assert "application/json" in config_up
    index_up = lines[index_of(lines, f"s3://{bucket}/index.html")]
    assert "--cache-control no-cache" in index_up

    invalidation = lines[index_of(lines, "cloudfront create-invalidation")]
    assert f"--distribution-id {OUTPUTS['DashboardDistributionId']}" in invalidation
    assert "--paths /index.html /" in invalidation
    assert OUTPUTS["DashboardUrl"] in result.stdout


def test_files_go_up_before_the_page_that_refers_to_them(sandbox: dict) -> None:
    run_script(sandbox)
    lines = calls(sandbox)
    assets = index_of(lines, "aws s3 sync")
    config = index_of(lines, "/config.json")
    page = index_of(lines, "/index.html --region")
    clear = index_of(lines, "create-invalidation")
    assert assets < config < page < clear


def test_it_reads_the_stack_it_was_asked_for(sandbox: dict) -> None:
    run_script(sandbox, "--stack", "other-stack", "--region", "eu-west-1")
    lines = calls(sandbox)
    describe = [line for line in lines if "describe-stacks" in line]
    assert len(describe) == 5  # one per output it needs
    assert all(
        "--stack-name other-stack" in line and "--region eu-west-1" in line for line in describe
    )
    assert all("--region eu-west-1" in line for line in lines if line.startswith("aws "))


# --- config.json and the secret ----------------------------------------------------------------


def test_config_json_has_the_api_address_and_key_and_lives_only_in_dist(sandbox: dict) -> None:
    run_script(sandbox)
    config_file = sandbox["dashboard"] / "dist" / "config.json"
    assert json.loads(config_file.read_text()) == {
        "apiUrl": "https://abc123.execute-api.ap-south-1.amazonaws.com/v1",  # slash removed
        "apiKey": API_KEY,
    }
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600  # not readable by other users
    assert not (sandbox["dashboard"] / "public" / "config.json").exists()  # never the source tree
    uploaded = json.loads((sandbox["uploads"] / "config.json").read_text())
    assert uploaded["apiKey"] == API_KEY


def test_the_api_key_is_never_printed_or_passed_on_a_command_line(sandbox: dict) -> None:
    result = run_script(sandbox)
    assert API_KEY not in result.stdout
    assert API_KEY not in result.stderr
    assert API_KEY not in "\n".join(calls(sandbox))  # only the key's ID is ever an argument


def test_the_key_is_fetched_by_id_with_its_value_requested(sandbox: dict) -> None:
    run_script(sandbox)
    fetch = calls(sandbox)[index_of(calls(sandbox), "apigateway get-api-key")]
    assert f"--api-key {OUTPUTS['ApiKeyId']}" in fetch
    assert "--include-value" in fetch


@pytest.mark.parametrize("bad_key", ['has"quote', "has space", "back\\slash"])
def test_a_key_that_could_break_the_json_is_refused_before_building(
    sandbox: dict, bad_key: str
) -> None:
    result = run_script(sandbox, extra_env={"FAKE_API_KEY": bad_key})
    assert result.returncode != 0
    assert "not allowed" in result.stderr
    assert not any(line.startswith("npm") for line in calls(sandbox))


def test_an_api_address_that_is_not_https_is_refused(sandbox: dict) -> None:
    sandbox["outputs"].write_text(
        sandbox["outputs"].read_text().replace("https://abc123", "http://abc123")
    )
    result = run_script(sandbox)
    assert result.returncode != 0
    assert "https://" in result.stderr
    assert not any(line.startswith("npm") for line in calls(sandbox))


# --- dry run -----------------------------------------------------------------------------------


def test_a_dry_run_builds_but_uploads_and_clears_nothing(sandbox: dict) -> None:
    result = run_script(sandbox, "--dry-run")
    assert result.returncode == 0, result.stderr
    lines = calls(sandbox)
    assert any("npm run build" in line for line in lines)
    assert not any(line.startswith("aws s3") for line in lines)
    assert not any("cloudfront" in line for line in lines)
    assert (sandbox["dashboard"] / "dist" / "config.json").exists()
    assert "Dry run" in result.stdout


# --- things that go wrong ------------------------------------------------------------------------


def test_a_missing_stack_output_says_to_deploy_first_and_changes_nothing(sandbox: dict) -> None:
    kept = [
        line for line in sandbox["outputs"].read_text().splitlines() if "Distribution" not in line
    ]
    sandbox["outputs"].write_text("\n".join(kept) + "\n")
    result = run_script(sandbox)
    assert result.returncode != 0
    assert "DashboardDistributionId" in result.stderr
    assert "sam deploy" in result.stderr
    lines = calls(sandbox)
    assert not any(line.startswith(("npm", "aws s3", "aws cloudfront")) for line in lines)


def test_an_unreadable_stack_is_reported_clearly(sandbox: dict) -> None:
    result = run_script(sandbox, extra_env={"FAKE_DESCRIBE_FAILS": "1"})
    assert result.returncode != 0
    assert "could not read stack 'gradeflow'" in result.stderr
    assert not any(line.startswith("npm") for line in calls(sandbox))


@pytest.mark.parametrize("failing", ["FAKE_TEST_FAILS", "FAKE_BUILD_FAILS"])
def test_a_failing_test_or_build_uploads_nothing(sandbox: dict, failing: str) -> None:
    result = run_script(sandbox, extra_env={failing: "1"})
    assert result.returncode != 0
    assert not any(line.startswith(("aws s3", "aws cloudfront")) for line in calls(sandbox))


def test_without_the_aws_cli_it_says_so(sandbox: dict, tmp_path: Path) -> None:
    only_npm = tmp_path / "only_npm"
    only_npm.mkdir()
    shutil.copy(sandbox["bin"] / "npm", only_npm / "npm")
    shutil.copy(shutil.which("dirname") or "/usr/bin/dirname", only_npm / "dirname")
    result = run_script(sandbox, path=str(only_npm))
    assert result.returncode != 0
    assert "AWS CLI is not installed" in result.stderr


def test_unknown_options_are_rejected_and_help_works(sandbox: dict) -> None:
    bad = run_script(sandbox, "--wat")
    assert bad.returncode != 0 and "unknown option" in bad.stderr
    helped = run_script(sandbox, "--help")
    assert helped.returncode == 0
    assert "--dry-run" in helped.stdout
    assert calls(sandbox) == []  # help touches nothing


# --- the repository ----------------------------------------------------------------------------


def test_the_script_is_executable_and_the_secret_files_are_ignored_by_git() -> None:
    assert os.access(SCRIPT, os.X_OK)
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert "dashboard/dist/" in ignored  # config.json with the key is written there
    assert "dashboard/public/config.json" in ignored  # and the local-development copy
