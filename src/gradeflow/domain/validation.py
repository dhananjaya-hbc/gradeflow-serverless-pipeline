"""Validation rules for student results files (CSV or JSON).

Two levels of problems:
- File-level (unreadable, empty, wrong columns): `parse_rows` raises `FileValidationError`.
- Row-level (bad score, missing value, ...): `validate_rows` counts and reports them.

Pure functions only: no AWS, no env vars, no clock (the current year is passed in).
"""

import csv
import io
import json
import math
import re
from dataclasses import dataclass

REQUIRED_COLUMNS = ("student_id", "student_name", "class", "subject", "term", "year", "score")
VALID_TERMS = frozenset({"T1", "T2", "T3"})
MIN_YEAR = 2000
MIN_SCORE, MAX_SCORE = 0.0, 100.0
STUDENT_ID_PATTERN = re.compile(r"S\d{5}")
MAX_REPORTED_ERRORS = 20  # keep the report small enough for one DynamoDB item

Row = dict[str, object]


class FileValidationError(Exception):
    """The whole file is unusable (unreadable, empty, or missing columns)."""


@dataclass(frozen=True)
class ValidationReport:
    """Result of validating every row of a file."""

    total_rows: int
    valid_rows: int
    row_errors: tuple[str, ...]  # first MAX_REPORTED_ERRORS problems, e.g. "row 3: ..."

    @property
    def invalid_rows(self) -> int:
        return self.total_rows - self.valid_rows

    @property
    def valid_ratio(self) -> float:
        return self.valid_rows / self.total_rows if self.total_rows else 0.0


def parse_rows(content: bytes, filename: str) -> list[Row]:
    """Parse CSV or JSON bytes into a list of row dicts, based on the file extension.

    Raises:
        FileValidationError: unsupported extension, undecodable content, invalid JSON,
            no data rows, or required columns missing.
    """
    try:
        text = content.decode("utf-8-sig")  # -sig strips the BOM Excel adds to CSVs
    except UnicodeDecodeError as exc:
        raise FileValidationError("file is not valid UTF-8 text") from exc

    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension == "csv":
        rows, columns = _parse_csv(text)
    elif extension == "json":
        rows, columns = _parse_json(text)
    else:
        raise FileValidationError(f"unsupported file type '.{extension}' (expected .csv or .json)")

    if not rows:
        raise FileValidationError("file contains no data rows")
    missing = [column for column in REQUIRED_COLUMNS if column not in columns]
    if missing:
        raise FileValidationError(f"missing required columns: {', '.join(missing)}")
    return rows


def _parse_csv(text: str) -> tuple[list[Row], set[str]]:
    reader = csv.DictReader(io.StringIO(text))
    rows: list[Row] = list(reader)
    columns = {name.strip() for name in reader.fieldnames or []}
    return [{key.strip(): value for key, value in row.items() if key} for row in rows], columns


def _parse_json(text: str) -> tuple[list[Row], set[str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FileValidationError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise FileValidationError("JSON must be an array of objects")
    columns = {key for item in data for key in item}
    return data, columns


def validate_row(row: Row, current_year: int) -> list[str]:
    """Return a list of problems with one row (empty list = valid row)."""
    problems = [f"missing {column}" for column in REQUIRED_COLUMNS if _is_missing(row.get(column))]

    student_id = row.get("student_id")
    if not _is_missing(student_id) and not STUDENT_ID_PATTERN.fullmatch(str(student_id).strip()):
        problems.append(f"student_id '{student_id}' must be 'S' + 5 digits")

    term = row.get("term")
    if not _is_missing(term) and str(term).strip() not in VALID_TERMS:
        problems.append(f"term '{term}' must be one of {', '.join(sorted(VALID_TERMS))}")

    year = row.get("year")
    if not _is_missing(year):
        year_number = _to_int(year)
        if year_number is None or not MIN_YEAR <= year_number <= current_year:
            problems.append(f"year '{year}' must be an integer {MIN_YEAR}-{current_year}")

    score = row.get("score")
    if not _is_missing(score):
        score_number = _to_float(score)
        if score_number is None or not MIN_SCORE <= score_number <= MAX_SCORE:
            problems.append(f"score '{score}' must be a number {MIN_SCORE:g}-{MAX_SCORE:g}")

    return problems


def split_valid_rows(rows: list[Row], current_year: int) -> tuple[list[Row], ValidationReport]:
    """Return the valid rows (for cleaning) plus a summary report. Row numbers start at 1."""
    valid: list[Row] = []
    errors: list[str] = []
    for number, row in enumerate(rows, start=1):
        problems = validate_row(row, current_year)
        if not problems:
            valid.append(row)
        elif len(errors) < MAX_REPORTED_ERRORS:
            errors.append(f"row {number}: {'; '.join(problems)}")
    report = ValidationReport(total_rows=len(rows), valid_rows=len(valid), row_errors=tuple(errors))
    return valid, report


def validate_rows(rows: list[Row], current_year: int) -> ValidationReport:
    """Validate every row and return only the summary report."""
    return split_valid_rows(rows, current_year)[1]


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _to_int(value: object) -> int | None:
    """Accept 2025, "2025" or 2025.0; reject booleans, decimals and text."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _to_float(value: object) -> float | None:
    """Accept numbers or numeric strings; reject booleans, NaN and infinity."""
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
