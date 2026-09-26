"""Unit tests for the pure validation rules (no AWS)."""

import json

import pytest

from gradeflow.domain.validation import (
    MAX_REPORTED_ERRORS,
    FileValidationError,
    ValidationReport,
    parse_rows,
    validate_row,
    validate_rows,
)

YEAR = 2026
HEADER = "student_id,student_name,class,subject,term,year,score\n"
GOOD_ROW = {
    "student_id": "S00001",
    "student_name": "Amal Perera",
    "class": "10-A",
    "subject": "Maths",
    "term": "T1",
    "year": "2025",
    "score": "78.5",
}


def row(**overrides: object) -> dict[str, object]:
    return {**GOOD_ROW, **overrides}


# --- parse_rows: file-level checks -------------------------------------------------


def test_parse_csv() -> None:
    rows = parse_rows((HEADER + "S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n").encode(), "a.csv")
    assert rows == [GOOD_ROW]


def test_parse_csv_strips_excel_bom_and_header_spaces() -> None:
    content = "﻿" + HEADER.replace(",", " , ") + "S00001,Amal Perera,10-A,Maths,T1,2025,78.5\n"
    assert parse_rows(content.encode(), "a.csv") == [GOOD_ROW]


def test_parse_json() -> None:
    data = [{**GOOD_ROW, "year": 2025, "score": 78.5}]
    assert parse_rows(json.dumps(data).encode(), "a.JSON") == data


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        (b"", "a.csv", "no data rows"),
        (HEADER.encode(), "a.csv", "no data rows"),
        (b"[]", "a.json", "no data rows"),
        (b"student_id,score\nS00001,50\n", "a.csv", "missing required columns"),
        (b"{not json", "a.json", "invalid JSON"),
        (b'{"student_id": "S00001"}', "a.json", "array of objects"),
        (b"[1, 2]", "a.json", "array of objects"),
        (b"\xff\xfe\x00", "a.csv", "not valid UTF-8"),
        (b"anything", "a.xlsx", "unsupported file type"),
        (b"anything", "noextension", "unsupported file type"),
    ],
)
def test_parse_rows_file_level_failures(content: bytes, filename: str, message: str) -> None:
    with pytest.raises(FileValidationError, match=message):
        parse_rows(content, filename)


# --- validate_row: row-level rules -------------------------------------------------


def test_good_row_is_valid() -> None:
    assert validate_row(GOOD_ROW, YEAR) == []


def test_numeric_json_values_are_valid() -> None:
    assert validate_row(row(year=2025, score=100), YEAR) == []


@pytest.mark.parametrize("column", list(GOOD_ROW))
@pytest.mark.parametrize("empty", [None, "", "   "])
def test_missing_value(column: str, empty: object) -> None:
    assert validate_row(row(**{column: empty}), YEAR) == [f"missing {column}"]


def test_absent_key_is_missing() -> None:
    incomplete = {k: v for k, v in GOOD_ROW.items() if k != "class"}
    assert validate_row(incomplete, YEAR) == ["missing class"]


@pytest.mark.parametrize("student_id", ["S123", "X00007", "S000012", "s00001", "S0000A"])
def test_bad_student_id(student_id: str) -> None:
    assert "must be 'S' + 5 digits" in validate_row(row(student_id=student_id), YEAR)[0]


@pytest.mark.parametrize("term", ["T4", "t1", "Term 1"])
def test_bad_term(term: str) -> None:
    assert "term" in validate_row(row(term=term), YEAR)[0]


@pytest.mark.parametrize("year", ["1999", "2027", "20x5", "2025.5", True])
def test_bad_year(year: object) -> None:
    assert "year" in validate_row(row(year=year), YEAR)[0]


@pytest.mark.parametrize("year", ["2000", "2026", 2025.0, " 2025 "])
def test_year_boundaries_and_formats(year: object) -> None:
    assert validate_row(row(year=year), YEAR) == []


@pytest.mark.parametrize("score", ["105", "120.5", "-5", "abc", "nan", "inf", False])
def test_bad_score(score: object) -> None:
    assert "score" in validate_row(row(score=score), YEAR)[0]


@pytest.mark.parametrize("score", ["0", "100", 0, 100.0])
def test_score_boundaries(score: object) -> None:
    assert validate_row(row(score=score), YEAR) == []


def test_multiple_problems_reported_together() -> None:
    problems = validate_row(row(student_id="bad", score="200"), YEAR)
    assert len(problems) == 2


def test_subject_casing_is_not_a_validation_error() -> None:
    """Normalising 'maths' -> 'Maths' is the cleaning step's job (Phase 2)."""
    assert validate_row(row(subject="  maths "), YEAR) == []


# --- validate_rows: summary report -------------------------------------------------


def test_validate_rows_counts_and_reports() -> None:
    rows = [GOOD_ROW, row(score="150"), GOOD_ROW, row(term=None)]
    report = validate_rows(rows, YEAR)
    assert report.total_rows == 4
    assert report.valid_rows == 2
    assert report.invalid_rows == 2
    assert report.valid_ratio == 0.5
    assert report.row_errors[0].startswith("row 2: score '150'")
    assert report.row_errors[1] == "row 4: missing term"


def test_reported_errors_are_capped() -> None:
    report = validate_rows([row(score="999")] * (MAX_REPORTED_ERRORS + 5), YEAR)
    assert report.invalid_rows == MAX_REPORTED_ERRORS + 5
    assert len(report.row_errors) == MAX_REPORTED_ERRORS


def test_valid_ratio_of_empty_report_is_zero() -> None:
    assert ValidationReport(total_rows=0, valid_rows=0, row_errors=()).valid_ratio == 0.0
