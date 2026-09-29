"""Statistics over cleaned results: per subject/term, overall, top/bottom students, classes.

Pure pandas. All numbers are rounded to 2 decimals; pass_rate is a percentage (0-100).
"""

from dataclasses import dataclass

import pandas as pd

DEFAULT_TOP_N = 5


@dataclass(frozen=True)
class ScoreSummary:
    """Descriptive statistics for a group of scores."""

    count: int
    mean: float
    median: float
    std: float  # sample standard deviation; 0.0 for a single score
    min: float
    max: float
    pass_rate: float  # % of scores >= pass mark


@dataclass(frozen=True)
class SubjectTermStats:
    subject: str
    term: str
    summary: ScoreSummary


@dataclass(frozen=True)
class StudentAverage:
    student_id: str
    student_name: str
    average: float


@dataclass(frozen=True)
class DatasetStats:
    """Everything the stats step produces for one dataset."""

    overall: ScoreSummary
    student_count: int
    by_subject_term: tuple[SubjectTermStats, ...]  # sorted by subject, then term
    top_students: tuple[StudentAverage, ...]  # best average first
    bottom_students: tuple[StudentAverage, ...]  # worst average first
    class_averages: dict[str, float]  # class -> mean of all its scores


def summarize_scores(scores: pd.Series, pass_mark: float) -> ScoreSummary:
    """Count, mean, median, std, min, max and pass rate for a non-empty series of scores."""
    std = scores.std()  # pandas default ddof=1 (sample std); NaN for a single value
    return ScoreSummary(
        count=int(scores.count()),
        mean=_round(scores.mean()),
        median=_round(scores.median()),
        std=0.0 if pd.isna(std) else _round(std),
        min=_round(scores.min()),
        max=_round(scores.max()),
        pass_rate=_round((scores >= pass_mark).mean() * 100),
    )


def compute_stats(data: pd.DataFrame, pass_mark: float, top_n: int = DEFAULT_TOP_N) -> DatasetStats:
    """Compute all statistics for a cleaned dataset (columns as produced by clean_rows).

    Raises:
        ValueError: if `data` has no rows.
    """
    if data.empty:
        raise ValueError("cannot compute statistics for an empty dataset")

    by_subject_term = tuple(
        SubjectTermStats(subject=subject, term=term, summary=summarize_scores(group, pass_mark))
        for (subject, term), group in data.groupby(["subject", "term"])["score"]
    )
    students = student_averages(data)
    return DatasetStats(
        overall=summarize_scores(data["score"], pass_mark),
        student_count=len(students),
        by_subject_term=by_subject_term,
        top_students=tuple(students[:top_n]),
        bottom_students=tuple(sorted(students, key=lambda s: (s.average, s.student_id))[:top_n]),
        class_averages={
            name: _round(mean) for name, mean in data.groupby("class")["score"].mean().items()
        },
    )


def student_averages(data: pd.DataFrame) -> list[StudentAverage]:
    """Average score per student across all subjects/terms, best first (ties: by student_id).

    If one ID appears with several names, the most common name is used.
    """
    grouped = data.groupby("student_id").agg(
        student_name=("student_name", lambda names: names.mode().iloc[0]),
        average=("score", "mean"),
    )
    grouped = grouped.reset_index().sort_values(["average", "student_id"], ascending=[False, True])
    return [
        StudentAverage(row.student_id, row.student_name, _round(row.average))
        for row in grouped.itertuples()
    ]


def _round(value: float) -> float:
    return round(float(value), 2)
