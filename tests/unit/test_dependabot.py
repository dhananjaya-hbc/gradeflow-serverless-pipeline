"""Dependabot's configuration, and GitHub's own schema for every file under .github/.

Two kinds of mistake would otherwise go unnoticed until a push: a configuration GitHub rejects
(Dependabot silently does nothing), and a dependency manifest that no update entry covers (it
quietly goes stale).
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPENDABOT_PATH = ROOT / ".github" / "dependabot.yml"
CONFIG: dict[str, Any] = yaml.safe_load(DEPENDABOT_PATH.read_text())
UPDATES: list[dict[str, Any]] = CONFIG["updates"]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def check_schema(schema: str, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "check_jsonschema", "--builtin-schema", schema, str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def tracked(pattern: str) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", pattern], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [ROOT / name for name in result.stdout.split()]


def directories(update: dict[str, Any]) -> set[str]:
    return set(update.get("directories", [update["directory"]] if "directory" in update else []))


def covered(ecosystem: str) -> set[str]:
    return {d for u in UPDATES if u["package-ecosystem"] == ecosystem for d in directories(u)}


def as_directory(path: Path) -> str:
    relative = path.parent.relative_to(ROOT).as_posix()
    return "/" if relative == "." else f"/{relative}"


# --- GitHub's schemas -----------------------------------------------------------------------------


def test_dependabot_yml_is_valid_against_githubs_schema() -> None:
    result = check_schema("vendor.dependabot", DEPENDABOT_PATH)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_is_valid_against_githubs_schema(path: Path) -> None:
    result = check_schema("vendor.github-workflows", path)
    assert result.returncode == 0, result.stdout + result.stderr


# --- coverage -------------------------------------------------------------------------------------


def test_every_python_requirements_file_is_covered() -> None:
    manifests = {as_directory(p) for p in tracked("*requirements*.txt")}
    assert manifests == {"/", "/src", "/docker/anomalies"}  # the files really are where expected
    assert manifests <= covered("pip")


def test_the_dashboards_package_json_is_covered() -> None:
    manifests = {as_directory(p) for p in tracked("*package.json")}
    assert manifests == {"/dashboard"}
    assert manifests <= covered("npm")


def test_github_actions_are_covered_so_their_pinned_shas_do_not_go_stale() -> None:
    assert WORKFLOWS  # there are workflows to keep up to date
    assert "/" in covered("github-actions")


def test_no_ecosystem_is_configured_for_a_directory_that_does_not_exist() -> None:
    for update in UPDATES:
        for directory in directories(update):
            assert (ROOT / directory.lstrip("/")).is_dir(), (update["package-ecosystem"], directory)


def test_exactly_these_ecosystems_are_configured() -> None:
    assert {u["package-ecosystem"] for u in UPDATES} == {"github-actions", "pip", "npm"}


# --- behaviour ------------------------------------------------------------------------------------


@pytest.mark.parametrize("update", UPDATES, ids=lambda u: u["package-ecosystem"])
def test_updates_are_weekly_few_at_a_time_and_use_conventional_commit_prefixes(
    update: dict[str, Any],
) -> None:
    assert update["schedule"]["interval"] == "weekly"
    assert update["open-pull-requests-limit"] <= 5
    assert update["commit-message"]["prefix"] in {"ci", "build"}  # the project's commit types


@pytest.mark.parametrize("update", UPDATES, ids=lambda u: u["package-ecosystem"])
def test_updates_are_grouped_so_versions_that_must_match_move_together(
    update: dict[str, Any],
) -> None:
    groups = update["groups"]
    assert len(groups) == 1
    assert next(iter(groups.values()))["patterns"] == ["*"]


def test_actions_use_the_ci_prefix_and_packages_use_the_build_prefix() -> None:
    prefixes = {u["package-ecosystem"]: u["commit-message"]["prefix"] for u in UPDATES}
    assert prefixes == {"github-actions": "ci", "pip": "build", "npm": "build"}


def test_the_python_pins_that_must_agree_are_updated_in_one_pull_request() -> None:
    (pip,) = [u for u in UPDATES if u["package-ecosystem"] == "pip"]
    assert directories(pip) == {"/", "/src", "/docker/anomalies"}  # one entry, one group
    assert list(pip["groups"]) == ["python"]
