"""Unit tests for the rule-based anomaly detectors (pure pandas, no AWS)."""

import json
import random
import statistics

import pandas as pd
import pytest
from scripts.generate_sample_data import inject_problems, make_clean_rows, make_students

from gradeflow.domain.anomalies import (
    PATTERN_LATEST,
    PATTERN_SPIKE,
    PATTERN_SUSTAINED,
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
    assert anomaly.details == (
        "Maths: score rose from 34.5 (T1) to 77 (T2), a change of +42.5 points; "
        "T2 is the latest term, so it is not yet known whether this lasts"
    )
    assert anomaly.pattern == PATTERN_LATEST  # only two terms: nothing to compare against


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


# --- what happened after a jump: spike, sustained or latest --------------------------


def patterns(*scores: float, **kwargs: float) -> list[tuple[str, str, float]]:
    """(term, pattern, severity) of every jump for one student's Maths scores."""
    terms = ("T1", "T2", "T3")
    rows = [
        ("S00001", "Amal", "Maths", term, value) for term, value in zip(terms, scores, strict=False)
    ]
    return [(a.term, a.pattern, a.severity) for a in detect_term_jumps(frame(*rows), **kwargs)]


def test_a_breakthrough_that_stays_is_sustained() -> None:
    [(term, pattern, _)] = patterns(40, 75, 78)
    assert (term, pattern) == ("T2", PATTERN_SUSTAINED)


def test_a_rise_that_keeps_climbing_is_sustained_too() -> None:
    assert [p for _, p, _ in patterns(34, 77, 99)] == [PATTERN_SUSTAINED]


def test_a_spike_that_goes_back_is_flagged_on_both_sides() -> None:
    assert patterns(34, 77, 38) == [("T2", PATTERN_SPIKE, 43.0), ("T3", PATTERN_SPIKE, 39.0)]


def test_a_bad_term_that_recovers_is_a_spike_in_the_other_direction() -> None:
    [(term, pattern, _)] = patterns(70, 38, 68)  # the recovery of +30 is exactly the threshold
    assert (term, pattern) == ("T2", PATTERN_SPIKE)


def test_a_jump_in_the_latest_term_cannot_be_judged_yet() -> None:
    assert patterns(50, 52, 95) == [("T3", PATTERN_LATEST, 43.0)]


def test_a_jump_with_no_following_term_is_latest() -> None:
    assert [p for _, p, _ in patterns(34, 77)] == [PATTERN_LATEST]


def test_the_score_must_go_back_at_least_halfway_to_count_as_a_spike() -> None:
    """T1=30 and T2=80 is a change of 50, so half is 25: T3 must end within 25 of T1."""
    assert patterns(30, 80, 54)[0][1] == PATTERN_SPIKE  # 24 away from T1
    assert patterns(30, 80, 56)[0][1] == PATTERN_SUSTAINED  # 26 away from T1


def test_a_fall_that_goes_on_falling_is_sustained() -> None:
    assert patterns(80, 40, 35)[0][1] == PATTERN_SUSTAINED


def test_the_reason_says_what_happened_next() -> None:
    reasons = {a.term: a.details for a in detect_term_jumps(frame(*jump_rows(34.0, 77.0, 38.0)))}
    assert reasons["T2"].endswith("it then moved back to 38 in T3, so this looks like a spike")
    assert reasons["T3"].endswith("this reverses the earlier jump, so it looks like a spike")
    held = detect_term_jumps(frame(*jump_rows(40.0, 75.0, 78.0)))[0]
    assert held.details.endswith("it did not go back in T3 (78)")


def test_spikes_rank_above_lasting_changes_even_when_smaller() -> None:
    """A suspicious blip of 35 points matters more than a lasting change of 45 points."""
    sustained = Anomaly(AnomalyMethod.TERM_JUMP, "S00001", 45.0, "x", pattern=PATTERN_SUSTAINED)
    latest = Anomaly(AnomalyMethod.TERM_JUMP, "S00002", 40.0, "x", pattern=PATTERN_LATEST)
    spike = Anomaly(AnomalyMethod.TERM_JUMP, "S00003", 35.0, "x", pattern=PATTERN_SPIKE)
    assert rank_anomalies([sustained, latest, spike]) == [spike, latest, sustained]


def test_within_a_pattern_the_bigger_change_ranks_first() -> None:
    small = Anomaly(AnomalyMethod.TERM_JUMP, "S00001", 31.0, "x", pattern=PATTERN_SPIKE)
    big = Anomaly(AnomalyMethod.TERM_JUMP, "S00002", 50.0, "x", pattern=PATTERN_SPIKE)
    assert rank_anomalies([small, big]) == [big, small]


def test_other_methods_ignore_the_pattern_in_ranking() -> None:
    zscore = Anomaly(AnomalyMethod.ZSCORE, "S00001", 5.0, "x")
    zscore_big = Anomaly(AnomalyMethod.ZSCORE, "S00002", 9.0, "x")
    assert rank_anomalies([zscore, zscore_big]) == [zscore_big, zscore]


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
        pattern=PATTERN_SPIKE,
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


def test_sample_patterns_two_spikes_and_one_climb(sample: pd.DataFrame) -> None:
    """S00040 and S00055 rose then fell back (spikes); S00034 went 34 -> 77 -> 99 (sustained)."""
    by_student = {}
    for a in detect_term_jumps(sample):
        by_student.setdefault(a.student_id, set()).add(a.pattern)
    assert by_student == {
        "S00034": {PATTERN_SUSTAINED},
        "S00040": {PATTERN_SPIKE},
        "S00055": {PATTERN_SPIKE},
    }


def test_sample_ranking_puts_the_spikes_before_the_climb(sample: pd.DataFrame) -> None:
    jumps = [a for a in detect_rule_based(sample) if a.method == AnomalyMethod.TERM_JUMP]
    assert [(a.student_id, a.pattern) for a in jumps] == [
        ("S00040", PATTERN_SPIKE),
        ("S00055", PATTERN_SPIKE),
        ("S00040", PATTERN_SPIKE),
        ("S00055", PATTERN_SPIKE),
        ("S00034", PATTERN_SUSTAINED),
    ]
