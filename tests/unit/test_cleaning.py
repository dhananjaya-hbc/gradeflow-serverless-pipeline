"""Unit tests for the cleaning rules (pure pandas, no AWS)."""

import random

import pytest
from scripts.generate_sample_data import inject_problems, make_clean_rows, make_students

from gradeflow.domain.cleaning import clean_rows, normalize_subject
from gradeflow.domain.validation import split_valid_rows

ROW = {
    "student_id": "S00001",
    "student_name": "Amal Perera",
    "class": "10-A",
    "subject": "Maths",
    "term": "T1",
    "year": "2025",
    "score": "78.5",
}


def row(**overrides: object) -> dict[str, object]:
    return {**ROW, **overrides}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Maths", "Maths"),
        ("maths", "Maths"),
        ("MATHS", "Maths"),
        ("  maths ", "Maths"),
        ("social   studies", "Social Studies"),
        ("ict", "ICT"),
        ("Ict", "ICT"),
        ("pe", "PE"),
    ],
)
def test_normalize_subject(raw: str, expected: str) -> None:
    assert normalize_subject(raw) == expected


def test_trims_text_and_casts_types() -> None:
    result = clean_rows([row(student_name="  Amal Perera ", term=" T1")])
    cleaned = result.data.iloc[0]

    assert cleaned["student_name"] == "Amal Perera"
    assert cleaned["term"] == "T1"
    assert result.data["year"].dtype == "int64"
    assert result.data["score"].dtype == "float64"
    assert cleaned["score"] == 78.5


def test_output_has_exactly_the_schema_columns() -> None:
    result = clean_rows([row(extra_column="ignored")])
    assert list(result.data.columns) == [
        "student_id", "student_name", "class", "subject", "term", "year", "score",
    ]  # fmt: skip


def test_json_style_numbers_are_cast_too() -> None:
    result = clean_rows([row(year=2025.0, score=90)])
    assert result.data.iloc[0]["year"] == 2025
    assert result.data.iloc[0]["score"] == 90.0


def test_drops_exact_duplicates() -> None:
    result = clean_rows([ROW, ROW, row(term="T2")])
    assert result.duplicates_removed == 1
    assert len(result.data) == 2


def test_duplicates_are_detected_after_normalizing() -> None:
    """'maths' / '78.50' and 'Maths' / '78.5' are the same row once cleaned."""
    result = clean_rows([ROW, row(subject=" maths ", score="78.50")])
    assert result.duplicates_removed == 1


def test_same_student_different_name_is_not_a_duplicate() -> None:
    """Same ID with a different name is an anomaly (Phase 5), not a duplicate to drop."""
    result = clean_rows([ROW, row(student_name="Fake Duplicate")])
    assert result.duplicates_removed == 0


def test_index_is_reset_after_dropping() -> None:
    result = clean_rows([ROW, ROW, row(term="T3")])
    assert list(result.data.index) == [0, 1]


def test_empty_input() -> None:
    result = clean_rows([])
    assert result.data.empty
    assert result.duplicates_removed == 0


def test_sample_generator_output() -> None:
    """Seed 42 sample: 904 rows -> 894 valid -> 3 exact duplicates removed -> 891 clean."""
    rng = random.Random(42)
    rows, _ = inject_problems(make_clean_rows(make_students(60, rng), 2025, rng), rng)

    valid, report = split_valid_rows(rows, current_year=2026)
    result = clean_rows(valid)

    assert (report.total_rows, report.valid_rows) == (904, 894)
    assert result.duplicates_removed == 3
    assert len(result.data) == 891
    assert sorted(result.data["subject"].unique()) == [
        "English", "History", "ICT", "Maths", "Science",
    ]  # fmt: skip
