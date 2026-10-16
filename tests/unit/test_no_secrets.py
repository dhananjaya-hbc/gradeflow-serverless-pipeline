"""No AWS access key and no real account ID in any file the repository tracks.

The project rule is: never commit secrets, no AWS keys, no account IDs. This enforces it for every
tracked file (the workflow, the scripts, the docs, the tests), not only where a leak is likely.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Account IDs that are fine to appear: AWS's own published layer, and the documentation-style
# placeholders used in tests.
ALLOWED_ACCOUNTS = {
    "336392948345",  # AWS SDK for pandas managed layer (a public AWS-owned account)
    "111122223333",  # placeholder used in tests
    "123456789012",  # AWS documentation's example account
}
ACCESS_KEY = re.compile(r"\b(AKIA|ASIA|AIDA|AROA)[0-9A-Z]{12,}\b")
ARN_WITH_ACCOUNT = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):")
SECRET_ASSIGNMENT = re.compile(r"(?i)aws_?secret_?access_?key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{30,}")


def tracked_text_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    files = []
    for name in filter(None, result.stdout.split("\0")):
        path = ROOT / name
        if path.suffix in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".parquet", ".lock"}:
            continue
        files.append(path)
    return files


FILES = tracked_text_files()


def read(path: Path) -> str:
    try:
        return path.read_text()
    except (UnicodeDecodeError, FileNotFoundError):
        return ""  # binary or removed in the working tree: nothing to scan


def test_there_are_files_to_scan() -> None:
    assert len(FILES) > 100  # the scans below must not pass by looking at nothing


def test_no_aws_access_key_id_is_committed() -> None:
    hits = [str(p.relative_to(ROOT)) for p in FILES if ACCESS_KEY.search(read(p))]
    assert hits == []


def test_no_aws_secret_access_key_assignment_is_committed() -> None:
    hits = [str(p.relative_to(ROOT)) for p in FILES if SECRET_ASSIGNMENT.search(read(p))]
    assert hits == []


def test_no_real_account_id_appears_in_an_arn() -> None:
    hits = []
    for path in FILES:
        for account in ARN_WITH_ACCOUNT.findall(read(path)):
            if account not in ALLOWED_ACCOUNTS:
                hits.append(f"{path.relative_to(ROOT)}: {account}")
    assert hits == []


# The samples below are assembled at run time. Written out in full they would look like real
# credentials, and this very scan would (rightly) flag this file.
SAMPLE_KEYS = ["AKIA" + "IOSFODNN7EXAMPLE", "ASIA" + "Y34FZKBOKMUTVV7A"]
SAMPLE_SECRET = "AWS_SECRET_" + "ACCESS_KEY=" + "wJalrXUtnFEMI/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"
SAMPLE_ACCOUNT = "9999" + "8888" + "7777"  # made up


@pytest.mark.parametrize("sample", SAMPLE_KEYS)
def test_the_key_pattern_really_matches_a_key(sample: str) -> None:
    assert ACCESS_KEY.search(f"key = {sample}")


def test_the_secret_pattern_really_matches_an_assignment() -> None:
    assert SECRET_ASSIGNMENT.search(SAMPLE_SECRET)


def test_the_account_pattern_really_matches_an_arn_with_an_account() -> None:
    arn = f"arn:aws:iam::{SAMPLE_ACCOUNT}:role/x"
    assert ARN_WITH_ACCOUNT.findall(arn) == [SAMPLE_ACCOUNT]
    assert SAMPLE_ACCOUNT not in ALLOWED_ACCOUNTS
