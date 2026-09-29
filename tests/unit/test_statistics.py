"""Unit tests for statistics. Expected values are worked out by hand in the comments."""

import pandas as pd
import pytest

from gradeflow.domain.statistics import (
    ScoreSummary,
    StudentAverage,
    compute_stats,
    summarize_scores,
)

PASS_MARK = 50

# 4 students x 2 subjects, one term. Scores:
#   S00001 Amal   10-A  Maths 80  Science 90   -> average 85
#   S00002 Nimal  10-A  Maths 40  Science 30   -> average 35
#   S00003 Kasun  10-B  Maths 60  Science 70   -> average 65
#   S00004 Dilini 10-B  Maths 50  Science 46   -> average 48
STUDENTS = [
    ("S00001", "Amal Perera", "10-A", 80, 90),
    ("S00002", "Nimal Silva", "10-A", 40, 30),
    ("S00003", "Kasun Fernando", "10-B", 60, 70),
    ("S00004", "Dilini Herath", "10-B", 50, 46),
]


def make_data(students: list[tuple] = STUDENTS) -> pd.DataFrame:
    rows = [
        {
            "student_id": sid,
            "student_name": name,
            "class": cls,
            "subject": subject,
            "term": "T1",
            "year": 2025,
            "score": float(score),
        }
        for sid, name, cls, maths, science in students
        for subject, score in (("Maths", maths), ("Science", science))
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def stats():
    return compute_stats(make_data(), PASS_MARK, top_n=2)


def test_subject_term_stats(stats) -> None:
    maths, science = stats.by_subject_term
    # Maths T1: 80, 40, 60, 50
    #   mean   = 230 / 4 = 57.5
    #   median = (50 + 60) / 2 = 55
    #   std    = sqrt((22.5² + 17.5² + 2.5² + 7.5²) / 3) = sqrt(875 / 3) = 17.08
    #   pass   = 80, 60, 50 (>= 50, so 50 passes) -> 3/4 = 75%
    assert (maths.subject, maths.term) == ("Maths", "T1")
    assert maths.summary == ScoreSummary(
        count=4, mean=57.5, median=55.0, std=17.08, min=40.0, max=80.0, pass_rate=75.0
    )
    # Science T1: 90, 30, 70, 46
    #   mean   = 236 / 4 = 59
    #   median = (46 + 70) / 2 = 58
    #   std    = sqrt((31² + 29² + 11² + 13²) / 3) = sqrt(2092 / 3) = 26.41
    #   pass   = 90, 70 -> 2/4 = 50%
    assert (science.subject, science.term) == ("Science", "T1")
    assert science.summary == ScoreSummary(
        count=4, mean=59.0, median=58.0, std=26.41, min=30.0, max=90.0, pass_rate=50.0
    )


def test_overall_stats(stats) -> None:
    # All 8 scores sorted: 30 40 46 50 | 60 70 80 90
    #   mean   = 466 / 8 = 58.25
    #   median = (50 + 60) / 2 = 55
    #   std    = sqrt(2971.5 / 7) = sqrt(424.5) = 20.60
    #   pass   = 50 60 70 80 90 -> 5/8 = 62.5%
    assert stats.overall == ScoreSummary(
        count=8, mean=58.25, median=55.0, std=20.6, min=30.0, max=90.0, pass_rate=62.5
    )
    assert stats.student_count == 4


def test_top_and_bottom_students(stats) -> None:
    assert stats.top_students == (
        StudentAverage("S00001", "Amal Perera", 85.0),
        StudentAverage("S00003", "Kasun Fernando", 65.0),
    )
    assert stats.bottom_students == (
        StudentAverage("S00002", "Nimal Silva", 35.0),
        StudentAverage("S00004", "Dilini Herath", 48.0),
    )


def test_class_averages(stats) -> None:
    # 10-A: 80 + 90 + 40 + 30 = 240 / 4 = 60
    # 10-B: 60 + 70 + 50 + 46 = 226 / 4 = 56.5
    assert stats.class_averages == {"10-A": 60.0, "10-B": 56.5}


def test_default_top_n_is_five() -> None:
    students = [(f"S{i:05d}", f"Student {i}", "10-A", i * 10, i * 10) for i in range(1, 8)]
    result = compute_stats(make_data(students), PASS_MARK)
    assert [s.student_id for s in result.top_students] == [
        "S00007", "S00006", "S00005", "S00004", "S00003",
    ]  # fmt: skip
    assert [s.student_id for s in result.bottom_students] == [
        "S00001", "S00002", "S00003", "S00004", "S00005",
    ]  # fmt: skip


def test_ties_are_ordered_by_student_id_in_both_lists() -> None:
    students = [
        ("S00003", "C", "10-A", 70, 70),
        ("S00001", "A", "10-A", 70, 70),
        ("S00002", "B", "10-A", 70, 70),
    ]
    result = compute_stats(make_data(students), PASS_MARK, top_n=3)
    assert [s.student_id for s in result.top_students] == ["S00001", "S00002", "S00003"]
    assert [s.student_id for s in result.bottom_students] == ["S00001", "S00002", "S00003"]


def test_most_common_name_is_used_for_a_student() -> None:
    data = make_data()
    data.loc[len(data)] = ["S00001", "Fake Duplicate", "10-A", "Maths", "T3", 2025, 85.0]
    top = compute_stats(data, PASS_MARK, top_n=1).top_students[0]
    assert top.student_name == "Amal Perera"


def test_pass_mark_is_configurable() -> None:
    # Scores >= 60: 80, 60, 90, 70 -> 4/8 = 50%
    assert compute_stats(make_data(), pass_mark=60).overall.pass_rate == 50.0


def test_single_score_has_zero_std() -> None:
    summary = summarize_scores(pd.Series([72.0]), PASS_MARK)
    assert summary == ScoreSummary(
        count=1, mean=72.0, median=72.0, std=0.0, min=72.0, max=72.0, pass_rate=100.0
    )


def test_values_are_rounded_to_two_decimals() -> None:
    summary = summarize_scores(pd.Series([1.0, 2.0, 2.0]), PASS_MARK)
    assert summary.mean == 1.67  # 5 / 3 = 1.666...
    assert summary.pass_rate == 0.0


def test_groups_are_sorted_by_subject_then_term() -> None:
    data = make_data()
    later_term = data.assign(term="T2")
    result = compute_stats(pd.concat([later_term, data]), PASS_MARK)
    assert [(s.subject, s.term) for s in result.by_subject_term] == [
        ("Maths", "T1"), ("Maths", "T2"), ("Science", "T1"), ("Science", "T2"),
    ]  # fmt: skip


def test_empty_dataset_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        compute_stats(make_data([]), PASS_MARK)
