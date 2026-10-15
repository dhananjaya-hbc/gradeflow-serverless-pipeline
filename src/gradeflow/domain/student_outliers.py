"""Machine-learning anomaly detection: students whose overall pattern is unusual.

Each student is summarised by four numbers (see FEATURES) and an Isolation Forest scores how
easy each student is to "isolate" from the rest: unusual students need fewer random splits.
The most unusual `contamination` share of students is flagged, each with a reason in words.

Complements the rule-based detectors: those find specific bad values, this finds students
whose whole profile does not look like the others (for example one that swings wildly).
"""

from dataclasses import dataclass

import pandas as pd
from sklearn.ensemble import IsolationForest

from gradeflow.domain.anomalies import TERM_ORDER, Anomaly, AnomalyMethod

DEFAULT_CONTAMINATION = 0.05  # flag roughly the most unusual 5% of students
MIN_STUDENTS = 20  # fewer students than this is too little data to call anyone unusual
N_TREES = 200
RANDOM_STATE = 42  # fixed, so the same file always gives the same findings

FEATURES = (
    "mean_score",
    "std_across_subjects",
    "mean_abs_term_change",
    "max_abs_term_change",
)


@dataclass(frozen=True)
class StudentOutlierResult:
    """Findings, or the reason the detector did not run."""

    anomalies: list[Anomaly]
    skipped: str | None = None


def student_features(data: pd.DataFrame) -> pd.DataFrame:
    """One row per student with the four FEATURES (cleaned data in, no NaN out).

    - mean_score: average of all the student's scores
    - std_across_subjects: spread of the student's per-subject averages (0 if one subject)
    - mean_abs_term_change: average size of the change between consecutive terms
    - max_abs_term_change: the largest single change between consecutive terms
    """
    per_student = data.groupby("student_id")["score"]
    subject_means = data.groupby(["student_id", "subject"])["score"].mean()
    by_term = data.pivot_table(
        index=["student_id", "subject"], columns="term", values="score", aggfunc="mean"
    ).reindex(columns=TERM_ORDER)
    changes = by_term.diff(axis=1).abs()  # NaN where a term is missing: skipped by mean/max

    features = pd.DataFrame(
        {
            "mean_score": per_student.mean(),
            "std_across_subjects": subject_means.groupby("student_id").std(),
            "mean_abs_term_change": changes.mean(axis=1).groupby("student_id").mean(),
            "max_abs_term_change": changes.max(axis=1).groupby("student_id").max(),
        }
    )
    return features.fillna(0.0)[list(FEATURES)]


def detect_student_outliers(
    data: pd.DataFrame,
    contamination: float = DEFAULT_CONTAMINATION,
    min_students: int = MIN_STUDENTS,
) -> StudentOutlierResult:
    """Flag the most unusual students with an Isolation Forest.

    Raises:
        ValueError: if `contamination` is not in (0, 0.5].
    """
    if not 0.0 < contamination <= 0.5:
        raise ValueError(f"contamination must be in (0, 0.5], got {contamination}")
    features = student_features(data) if not data.empty else pd.DataFrame(columns=FEATURES)
    if len(features) < min_students:
        return StudentOutlierResult(
            [], skipped=f"only {len(features)} students, at least {min_students} are needed"
        )

    forest = IsolationForest(
        n_estimators=N_TREES, contamination=contamination, random_state=RANDOM_STATE
    ).fit(features)
    anomaly_score = pd.Series(-forest.score_samples(features), index=features.index)
    flagged = anomaly_score[forest.predict(features) == -1]

    names = data.drop_duplicates("student_id").set_index("student_id")["student_name"]
    typical, spread = features.mean(), features.std(ddof=0)
    anomalies = [
        Anomaly(
            method=AnomalyMethod.ISOLATION_FOREST,
            student_id=student_id,
            student_name=names.get(student_id),
            severity=round(float(score), 3),
            details=_reason(features.loc[student_id], typical, spread),
        )
        for student_id, score in flagged.sort_values(ascending=False).items()
    ]
    return StudentOutlierResult(anomalies)


def _reason(student: pd.Series, typical: pd.Series, spread: pd.Series) -> str:
    """Describe the feature in which this student differs most from the others."""
    z = ((student - typical) / spread.where(spread > 0)).fillna(0.0)
    feature = z.abs().idxmax()
    value, average, deviations = student[feature], typical[feature], abs(z[feature])
    direction = "above" if z[feature] > 0 else "below"
    summary = {
        "mean_score": f"average score {value:.1f}",
        "std_across_subjects": f"spread between subjects {value:.1f} points",
        "mean_abs_term_change": f"average change between terms {value:.1f} points",
        "max_abs_term_change": f"largest change between consecutive terms {value:.1f} points",
    }[feature]
    return (
        f"unusual student profile: {summary} is {deviations:.1f} standard deviations "
        f"{direction} the typical student ({average:.1f})"
    )
