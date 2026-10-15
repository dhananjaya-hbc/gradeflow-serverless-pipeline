"""Unit tests for the rule-based anomaly detectors (pure pandas, no AWS)."""

import json
import random
import statistics

import pandas as pd
import pytest
from scripts.generate_sample_data import inject_problems, make_clean_rows, make_students

from gradeflow.domain.anomalies import (
    Anomaly,
    AnomalyMethod,
    detect_duplicate_ids,
    detect_rule_based,
    detect_term_jumps,
    detect_zscore_outliers,
    rank_anomalies,
)
from gradeflow.domain.cleaning import clean_rows
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


def class_of(scores: list[float], subject: str = "Maths", term: str = "T1") -> list[tuple]:
    return [(f"S{i:05d}", f"Student {i}", subject, term, s) for i, s in enumerate(scores, 1)]


# --- z-score outliers ----------------------------------------------------------------


def test_zscore_flags_one_extreme_score() -> None:
    scores = [58.0, 62.0] * 14 + [60.0, 5.0]  # 30 students, the last one far below
    result = detect_zscore_outliers(frame(*class_of(scores)))

    [anomaly] = result
    assert (anomaly.method, anomaly.student_id) == (AnomalyMethod.ZSCORE, "S00030")
    assert (anomaly.subject, anomaly.term, anomaly.score) == ("Maths", "T1", 5.0)
    expected_z = abs((5.0 - statistics.mean(scores)) / statistics.stdev(scores))
    assert anomaly.severity == round(expected_z, 2)  # sample standard deviation
    assert expected_z > 3
    assert "below the mean" in anomaly.details and "Maths T1" in anomaly.details


def test_zscore_flags_high_outliers_too() -> None:
    scores = [50.0, 52.0] * 14 + [51.0, 99.0]
    [anomaly] = detect_zscore_outliers(frame(*class_of(scores)))
    assert anomaly.score == 99.0 and "above the mean" in anomaly.details


def test_zscore_returns_nothing_for_ordinary_scores() -> None:
    assert detect_zscore_outliers(frame(*class_of([50.0, 55.0, 60.0, 65.0, 70.0] * 6))) == []


def test_zscore_compares_within_subject_and_term_only() -> None:
    """Maths scores around 90 and Science around 30 are not outliers of each other."""
    rows = class_of([88.0, 92.0] * 10, "Maths") + class_of([28.0, 32.0] * 10, "Science")
    assert detect_zscore_outliers(frame(*rows)) == []


def test_zscore_skips_groups_without_spread() -> None:
    assert detect_zscore_outliers(frame(*class_of([50.0] * 20))) == []


def test_zscore_limit_is_configurable() -> None:
    scores = [40.0, 60.0] * 14 + [50.0, 82.0]  # the 82 is a mild outlier
    z = (82.0 - statistics.mean(scores)) / statistics.stdev(scores)
    assert 2.5 < z < 3.0  # the premise of this test

    data = frame(*class_of(scores))
    assert detect_zscore_outliers(data, limit=3.0) == []
    assert [a.score for a in detect_zscore_outliers(data, limit=2.5)] == [82.0]


def test_a_group_of_ten_cannot_exceed_a_zscore_of_three() -> None:
    """The largest possible |z| for n scores is (n - 1) / sqrt(n): 2.85 for n = 10."""
    assert detect_zscore_outliers(frame(*class_of([50.0] * 9 + [100.0]))) == []


def test_zscore_on_empty_data() -> None:
    assert detect_zscore_outliers(frame()) == []


# --- term jumps ----------------------------------------------------------------------


def jump_rows(t1: float, t2: float, t3: float | None = None, subject: str = "Maths") -> list:
    rows = [
        ("S00001", "Amal Perera", subject, "T1", t1),
        ("S00001", "Amal Perera", subject, "T2", t2),
    ]
    if t3 is not None:
        rows.append(("S00001", "Amal Perera", subject, "T3", t3))
    return rows


def test_a_rise_of_more_than_the_threshold_is_flagged() -> None:
    [anomaly] = detect_term_jumps(frame(*jump_rows(34.5, 77.0)))

    assert anomaly.method == AnomalyMethod.TERM_JUMP
    assert (anomaly.student_id, anomaly.student_name) == ("S00001", "Amal Perera")
    assert (anomaly.subject, anomaly.term, anomaly.score) == ("Maths", "T2", 77.0)
    assert anomaly.severity == 42.5
    assert (
        anomaly.details == "Maths: score rose from 34.5 (T1) to 77 (T2), a change of +42.5 points"
    )


def test_a_fall_is_flagged_too() -> None:
    [anomaly] = detect_term_jumps(frame(*jump_rows(80.0, 40.0)))
    assert anomaly.severity == 40.0 and "fell from 80 (T1) to 40 (T2)" in anomaly.details


def test_exactly_the_threshold_is_not_a_jump() -> None:
    assert detect_term_jumps(frame(*jump_rows(40.0, 70.0))) == []  # a change of exactly 30
    assert len(detect_term_jumps(frame(*jump_rows(40.0, 70.5)))) == 1  # 30.5 is


def test_only_consecutive_terms_count() -> None:
    """T1 to T3 differs by 35, but each step (15 and 20) is within the threshold."""
    assert detect_term_jumps(frame(*jump_rows(40.0, 55.0, 75.0))) == []


def test_a_jump_up_then_back_down_reports_both() -> None:
    result = detect_term_jumps(frame(*jump_rows(35.0, 80.0, 40.0)))
    assert [(a.term, a.severity) for a in result] == [("T2", 45.0), ("T3", 40.0)]


def test_a_missing_term_skips_the_pair() -> None:
    data = frame(("S00001", "Amal", "Maths", "T1", 30.0), ("S00001", "Amal", "Maths", "T3", 90.0))
    assert detect_term_jumps(data) == []


def test_subjects_are_independent() -> None:
    rows = jump_rows(35.0, 80.0, subject="Maths") + jump_rows(60.0, 62.0, subject="Science")
    result = detect_term_jumps(frame(*rows))
    assert [(a.subject, a.term) for a in result] == [("Maths", "T2")]


def test_jump_threshold_is_configurable() -> None:
    data = frame(*jump_rows(50.0, 65.0))
    assert detect_term_jumps(data, threshold=30.0) == []
    assert len(detect_term_jumps(data, threshold=10.0)) == 1


def test_term_jumps_on_empty_data() -> None:
    assert detect_term_jumps(frame()) == []


# --- duplicate IDs -------------------------------------------------------------------


def test_one_id_with_two_names_is_flagged() -> None:
    data = frame(
        ("S00001", "Amal Perera", "Maths", "T1", 60.0),
        ("S00001", "Fake Duplicate", "Maths", "T3", 60.0),
        ("S00002", "Nimal Silva", "Maths", "T1", 60.0),
    )

    [anomaly] = detect_duplicate_ids(data)

    assert (anomaly.method, anomaly.student_id, anomaly.severity) == (
        AnomalyMethod.DUPLICATE_ID,
        "S00001",
        2.0,
    )
    assert (
        anomaly.details
        == "student ID is used with 2 different names: 'Amal Perera', 'Fake Duplicate'"
    )


def test_the_same_name_on_many_rows_is_fine() -> None:
    data = frame(*[("S00001", "Amal Perera", "Maths", t, 60.0) for t in ("T1", "T2", "T3")])
    assert detect_duplicate_ids(data) == []


def test_three_names_give_severity_three() -> None:
    data = frame(*[("S00001", name, "Maths", "T1", 60.0) for name in ("A", "B", "C")])
    assert detect_duplicate_ids(data)[0].severity == 3.0


# --- combining, ordering, serialising -------------------------------------------------


def anomaly(method: AnomalyMethod, severity: float, student_id: str = "S00001") -> Anomaly:
    return Anomaly(method=method, student_id=student_id, severity=severity, details="x")


def test_ranking_puts_data_problems_first_then_rules_then_ml() -> None:
    mixed = [
        anomaly(AnomalyMethod.ISOLATION_FOREST, 0.99),
        anomaly(AnomalyMethod.ZSCORE, 9.0),
        anomaly(AnomalyMethod.TERM_JUMP, 31.0),
        anomaly(AnomalyMethod.DUPLICATE_ID, 2.0),
        anomaly(AnomalyMethod.TERM_JUMP, 45.0, "S00002"),
    ]
    ranked = rank_anomalies(mixed)
    assert [(a.method.value, a.severity) for a in ranked] == [
        ("duplicate_id", 2.0),
        ("term_jump", 45.0),
        ("term_jump", 31.0),
        ("zscore", 9.0),
        ("isolation_forest", 0.99),
    ]


def test_ranking_is_deterministic_for_ties() -> None:
    a, b = (
        anomaly(AnomalyMethod.ZSCORE, 4.0, "S00009"),
        anomaly(AnomalyMethod.ZSCORE, 4.0, "S00002"),
    )
    assert rank_anomalies([a, b]) == rank_anomalies([b, a]) == [b, a]


def test_detect_rule_based_combines_all_three_ranked() -> None:
    rows = jump_rows(35.0, 80.0) + [("S00001", "Fake Duplicate", "Maths", "T3", 70.0)]
    result = detect_rule_based(frame(*rows))
    assert [a.method.value for a in result] == ["duplicate_id", "term_jump"]


def test_anomaly_survives_a_json_round_trip() -> None:
    original = Anomaly(
        method=AnomalyMethod.TERM_JUMP,
        student_id="S00001",
        student_name="Amal",
        subject="Maths",
        term="T2",
        score=77.0,
        severity=42.5,
        details="Maths: score rose",
    )
    assert Anomaly.from_dict(json.loads(json.dumps(original.to_dict()))) == original


def test_anomaly_without_optional_fields_round_trips() -> None:
    original = anomaly(AnomalyMethod.DUPLICATE_ID, 2.0)
    assert Anomaly.from_dict(json.loads(json.dumps(original.to_dict()))) == original


# --- the planted anomalies of the sample generator ------------------------------------


@pytest.fixture(scope="module")
def sample() -> pd.DataFrame:
    rng = random.Random(42)
    rows, _ = inject_problems(make_clean_rows(make_students(60, rng), 2025, rng), rng)
    return clean_rows(split_valid_rows(rows, current_year=2026)[0]).data


def test_sample_term_jumps_include_all_three_planted_ones(sample: pd.DataFrame) -> None:
    found = {(a.student_id, a.subject, a.term) for a in detect_term_jumps(sample)}
    planted = {("S00034", "Science", "T2"), ("S00040", "Maths", "T2"), ("S00055", "Science", "T2")}
    assert planted <= found


def test_sample_term_jumps_are_exactly_these_five(sample: pd.DataFrame) -> None:
    """The three planted rises, plus the falls back to normal in T3 for two of them."""
    found = sorted((a.student_id, a.subject, a.term, a.severity) for a in detect_term_jumps(sample))
    assert found == [
        ("S00034", "Science", "T2", 42.6),
        ("S00040", "Maths", "T2", 46.9),
        ("S00040", "Maths", "T3", 39.1),
        ("S00055", "Science", "T2", 44.8),
        ("S00055", "Science", "T3", 34.8),
    ]


def test_sample_duplicate_id_is_found(sample: pd.DataFrame) -> None:
    [anomaly] = detect_duplicate_ids(sample)
    assert anomaly.student_id == "S00045"
    assert "Fake Duplicate" in anomaly.details


def test_sample_has_no_zscore_outliers(sample: pd.DataFrame) -> None:
    """The planted out-of-range scores are removed in validation, so nothing is extreme."""
    assert detect_zscore_outliers(sample) == []


def test_sample_rule_based_summary(sample: pd.DataFrame) -> None:
    result = detect_rule_based(sample)
    assert [a.method.value for a in result] == ["duplicate_id"] + ["term_jump"] * 5
