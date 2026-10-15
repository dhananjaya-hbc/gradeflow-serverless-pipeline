"""Unit tests for the Isolation Forest student outlier detector (no AWS)."""

import random
import statistics

import pandas as pd
import pytest
from scripts.generate_sample_data import inject_problems, make_clean_rows, make_students

from gradeflow.domain.anomalies import AnomalyMethod
from gradeflow.domain.cleaning import clean_rows
from gradeflow.domain.student_outliers import (
    FEATURES,
    MIN_STUDENTS,
    _reason,
    detect_student_outliers,
    student_features,
)
from gradeflow.domain.validation import split_valid_rows


def frame(*rows: tuple) -> pd.DataFrame:
    """Rows of (student_id, name, subject, term, score) as a cleaned-data DataFrame."""
    return pd.DataFrame(
        [
            {
                "student_id": sid,
                "student_name": name,
                "class": "10-A",
                "subject": subject,
                "term": term,
                "year": 2025,
                "score": float(score),
            }
            for sid, name, subject, term, score in rows
        ]
    )


def ordinary_class(count: int, offset: int = 1) -> list[tuple]:
    """`count` similar students: 2 subjects x 3 terms, scores near 60 with a small, fixed wobble."""
    rows = []
    for i in range(offset, offset + count):
        wobble = (i * 7) % 5 - 2  # -2 .. +2, deterministic
        for subject, base in (("Maths", 60), ("Science", 62)):
            for term, drift in (("T1", 0), ("T2", 1), ("T3", 2)):
                rows.append((f"S{i:05d}", f"Student {i}", subject, term, base + wobble + drift))
    return rows


WILD = [  # one student that swings wildly and scores low
    ("S99999", "Wild Student", "Maths", "T1", 20),
    ("S99999", "Wild Student", "Maths", "T2", 85),
    ("S99999", "Wild Student", "Maths", "T3", 25),
    ("S99999", "Wild Student", "Science", "T1", 80),
    ("S99999", "Wild Student", "Science", "T2", 15),
    ("S99999", "Wild Student", "Science", "T3", 70),
]


# --- features ------------------------------------------------------------------------


def test_features_are_calculated_by_hand() -> None:
    data = frame(
        ("S00001", "A", "Maths", "T1", 60), ("S00001", "A", "Maths", "T2", 70),
        ("S00001", "A", "Maths", "T3", 70),
        ("S00001", "A", "Science", "T1", 50), ("S00001", "A", "Science", "T2", 50),
        ("S00001", "A", "Science", "T3", 80),
        ("S00002", "B", "Maths", "T1", 80),
    )  # fmt: skip

    features = student_features(data)

    assert list(features.columns) == list(FEATURES)
    a = features.loc["S00001"]
    assert a["mean_score"] == pytest.approx(380 / 6)  # (60+70+70+50+50+80) / 6
    # per-subject means 66.67 and 60: sample std of two values = |difference| / sqrt(2)
    assert a["std_across_subjects"] == pytest.approx(statistics.stdev([200 / 3, 60.0]))
    # Maths changes 10, 0 -> mean 5; Science changes 0, 30 -> mean 15; student mean = 10
    assert a["mean_abs_term_change"] == pytest.approx(10.0)
    assert a["max_abs_term_change"] == pytest.approx(30.0)  # the Science T2 -> T3 change


def test_a_student_with_one_score_has_zero_for_spread_and_changes() -> None:
    b = student_features(frame(("S00002", "B", "Maths", "T1", 80))).loc["S00002"]
    assert b["mean_score"] == 80.0
    assert (b["std_across_subjects"], b["mean_abs_term_change"], b["max_abs_term_change"]) == (
        0,
        0,
        0,
    )


def test_non_consecutive_terms_do_not_count_as_a_change() -> None:
    data = frame(("S00001", "A", "Maths", "T1", 10), ("S00001", "A", "Maths", "T3", 90))
    assert student_features(data).loc["S00001", "max_abs_term_change"] == 0


def test_features_have_no_missing_values() -> None:
    features = student_features(frame(*ordinary_class(5)))
    assert not features.isna().any().any()
    assert len(features) == 5


# --- the detector --------------------------------------------------------------------


def test_an_obviously_unusual_student_is_flagged_with_a_reason() -> None:
    data = frame(*ordinary_class(40), *WILD)

    result = detect_student_outliers(data)

    assert result.skipped is None
    assert result.anomalies[0].student_id == "S99999"  # the most unusual comes first
    wild = result.anomalies[0]
    assert (wild.method, wild.student_name) == (AnomalyMethod.ISOLATION_FOREST, "Wild Student")
    assert wild.details.startswith("unusual student profile: ")
    assert "standard deviations" in wild.details and "typical student" in wild.details
    assert wild.subject is None and wild.term is None and wild.score is None


def realistic_class(students: int, seed: int = 3) -> pd.DataFrame:
    """Students with varied abilities and some noise, as the sample generator makes them."""
    rng = random.Random(seed)
    rows = make_clean_rows(make_students(students, rng), 2025, rng)
    return clean_rows(rows).data


def test_about_the_contamination_share_of_students_is_flagged() -> None:
    flagged = detect_student_outliers(realistic_class(60), contamination=0.05).anomalies
    assert 1 <= len(flagged) <= 6  # about 5% of 60 students

    more = detect_student_outliers(realistic_class(60), contamination=0.20).anomalies
    assert len(more) > len(flagged)


def test_identical_students_are_not_called_unusual() -> None:
    """With tied scores nobody falls below the cut-off, so nobody is flagged."""
    assert detect_student_outliers(frame(*ordinary_class(60))).anomalies == []


def test_severity_is_rounded_and_most_unusual_first() -> None:
    result = detect_student_outliers(frame(*ordinary_class(40), *WILD), contamination=0.15)
    severities = [a.severity for a in result.anomalies]
    assert severities == sorted(severities, reverse=True)
    assert all(0 < s < 1 and s == round(s, 3) for s in severities)


def test_the_same_data_always_gives_the_same_findings() -> None:
    data = frame(*ordinary_class(40), *WILD)
    first = detect_student_outliers(data).anomalies
    assert detect_student_outliers(data).anomalies == first


def test_too_few_students_skips_with_a_reason() -> None:
    result = detect_student_outliers(frame(*ordinary_class(MIN_STUDENTS - 1)))
    assert result.anomalies == []
    assert result.skipped == f"only {MIN_STUDENTS - 1} students, at least {MIN_STUDENTS} are needed"


def test_exactly_the_minimum_number_of_students_runs() -> None:
    assert detect_student_outliers(frame(*ordinary_class(MIN_STUDENTS))).skipped is None


def test_empty_data_is_skipped() -> None:
    assert detect_student_outliers(frame()).skipped == (
        f"only 0 students, at least {MIN_STUDENTS} are needed"
    )


@pytest.mark.parametrize("contamination", [0, -0.1, 0.51, 1])
def test_contamination_must_be_between_zero_and_a_half(contamination: float) -> None:
    with pytest.raises(ValueError, match="contamination"):
        detect_student_outliers(frame(*ordinary_class(30)), contamination=contamination)


# --- the reason text -----------------------------------------------------------------


def reason_for(**student: float) -> str:
    typical = pd.Series({"mean_score": 60.0, "std_across_subjects": 5.0,
                         "mean_abs_term_change": 4.0, "max_abs_term_change": 8.0})  # fmt: skip
    spread = pd.Series({"mean_score": 10.0, "std_across_subjects": 2.0,
                        "mean_abs_term_change": 2.0, "max_abs_term_change": 4.0})  # fmt: skip
    row = typical.copy()
    row.update(pd.Series(student))
    return _reason(row, typical, spread)


def test_reason_names_the_most_extreme_feature_low_score() -> None:
    assert reason_for(mean_score=36.0) == (
        "unusual student profile: average score 36.0 is 2.4 standard deviations "
        "below the typical student (60.0)"
    )


def test_reason_names_a_large_term_change() -> None:
    reason = reason_for(max_abs_term_change=26.0)  # 4.5 standard deviations above 8
    assert "largest change between consecutive terms 26.0 points" in reason
    assert "4.5 standard deviations above the typical student (8.0)" in reason


def test_reason_picks_the_biggest_deviation_among_several() -> None:
    reason = reason_for(mean_score=70.0, mean_abs_term_change=12.0)  # z = 1.0 and 4.0
    assert "average change between terms 12.0 points" in reason


def test_reason_handles_features_with_no_spread() -> None:
    typical = pd.Series({f: 1.0 for f in FEATURES})
    spread = pd.Series({f: 0.0 for f in FEATURES})  # everyone identical
    assert "unusual student profile" in _reason(typical, typical, spread)


# --- the planted sample, and how reliable the detector is -----------------------------


def make_sample(seed: int, students: int = 60) -> tuple[pd.DataFrame, set[str]]:
    rng = random.Random(seed)
    rows, report = inject_problems(make_clean_rows(make_students(students, rng), 2025, rng), rng)
    planted = {line.split()[0] for line in report["term_jump_anomalies"]}
    return clean_rows(split_valid_rows(rows, current_year=2026)[0]).data, planted


def test_seed_42_flags_exactly_the_three_planted_students() -> None:
    data, planted = make_sample(42)
    flagged = detect_student_outliers(data).anomalies
    assert {a.student_id for a in flagged} == planted == {"S00034", "S00040", "S00055"}


def test_default_settings_find_most_planted_students_across_seeds() -> None:
    """Measured: 82% on average for a class of 60 (all three in 11 of 20 seeds).

    The forest is a probabilistic second opinion, so this pins a floor, not perfection.
    """
    recalls = []
    for seed in range(1, 21):
        data, planted = make_sample(seed)
        flagged = {a.student_id for a in detect_student_outliers(data).anomalies}
        recalls.append(len(planted & flagged) / len(planted))
    assert statistics.mean(recalls) >= 0.7


def test_larger_classes_find_every_planted_student() -> None:
    """Measured: 300 students at 5% found all three planted students in 20 of 20 seeds."""
    for seed in range(1, 8):
        data, planted = make_sample(seed, students=300)
        flagged = {a.student_id for a in detect_student_outliers(data).anomalies}
        assert planted <= flagged, f"seed {seed}"
