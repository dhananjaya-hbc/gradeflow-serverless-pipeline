"""Cleaning rules: turn validated rows into a tidy, typed DataFrame.

Runs after validation, so every row already has all required fields with valid values.
"""

from dataclasses import dataclass

import pandas as pd

from gradeflow.domain.validation import REQUIRED_COLUMNS, Row

TEXT_COLUMNS = ["student_id", "student_name", "class", "subject", "term"]
# Title Case would turn these into "Ict" / "Pe", so they stay upper case.
SUBJECT_ACRONYMS = frozenset({"ICT", "IT", "PE"})


@dataclass(frozen=True)
class CleaningResult:
    """Cleaned data plus what the cleaning step changed."""

    data: pd.DataFrame
    duplicates_removed: int


def normalize_subject(subject: str) -> str:
    """'  maths ' -> 'Maths', 'social studies' -> 'Social Studies', 'ict' -> 'ICT'."""
    words = subject.split()
    return " ".join(w.upper() if w.upper() in SUBJECT_ACRONYMS else w.capitalize() for w in words)


def clean_rows(rows: list[Row]) -> CleaningResult:
    """Trim text, normalise subjects, cast types, then drop exact duplicates.

    Duplicates are dropped *after* normalising, so 'maths' and 'Maths' rows that are
    otherwise identical count as the same row.
    """
    data = pd.DataFrame(rows, columns=list(REQUIRED_COLUMNS))  # extra columns are dropped

    for column in TEXT_COLUMNS:
        data[column] = data[column].astype(str).str.strip()
    data["subject"] = data["subject"].map(normalize_subject)
    data["year"] = pd.to_numeric(data["year"]).astype("int64")
    data["score"] = pd.to_numeric(data["score"]).astype("float64")

    before = len(data)
    data = data.drop_duplicates().reset_index(drop=True)
    return CleaningResult(data=data, duplicates_removed=before - len(data))
