"""Rule-based anomaly detection over cleaned results (pure pandas, no AWS).

Three detectors, each returning a list of `Anomaly`:
- z-score outliers: a score far from the others in the same subject and term
- term jumps: a student's score in one subject changes by more than a threshold between
  consecutive terms (T1 to T2, T2 to T3)
- duplicate IDs: one student ID used with different names

The machine-learning detector lives in `student_outliers.py`.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pandas as pd

from gradeflow.domain.validation import VALID_TERMS

DEFAULT_ZSCORE_LIMIT = 3.0
DEFAULT_JUMP_THRESHOLD = 30.0
TERM_ORDER = sorted(VALID_TERMS)  # T1, T2, T3


class AnomalyMethod(StrEnum):
    """Which detector found the anomaly."""

    ZSCORE = "zscore"
    TERM_JUMP = "term_jump"
    DUPLICATE_ID = "duplicate_id"
    ISOLATION_FOREST = "isolation_forest"


# What happened to a score after it jumped. A spike (it went back) is more suspicious than a
# change that held, and a change in the latest term cannot be judged yet.
PATTERN_SPIKE = "spike"
PATTERN_LATEST = "latest"
PATTERN_SUSTAINED = "sustained"
_PATTERN_RANK = {PATTERN_SPIKE: 0, PATTERN_LATEST: 1, PATTERN_SUSTAINED: 2}

# When too many anomalies are found only the first N are stored, so the order matters:
# data-integrity problems first, then rule findings, then the statistical (ML) ones.
_PRIORITY = {
    AnomalyMethod.DUPLICATE_ID: 0,
    AnomalyMethod.TERM_JUMP: 1,
    AnomalyMethod.ZSCORE: 2,
    AnomalyMethod.ISOLATION_FOREST: 3,
}


@dataclass(frozen=True)
class Anomaly:
    """One finding. `severity` is comparable only within the same method."""

    method: AnomalyMethod
    student_id: str
    severity: float  # |z|, size of the jump, number of names, or the forest's anomaly score
    details: str  # a human-readable reason
    student_name: str | None = None
    subject: str | None = None
    term: str | None = None
    score: float | None = None  # the offending score, when there is one
    pattern: str | None = None  # term jumps only: spike, sustained or latest

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method.value,
            "student_id": self.student_id,
            "student_name": self.student_name,
            "subject": self.subject,
            "term": self.term,
            "score": self.score,
            "severity": self.severity,
            "pattern": self.pattern,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Anomaly":
        return cls(**{**data, "method": AnomalyMethod(data["method"])})


def rank_anomalies(anomalies: list[Anomaly]) -> list[Anomaly]:
    """Most important first: method, then jump pattern, then severity, then a stable tie-break."""
    return sorted(
        anomalies,
        key=lambda a: (
            _PRIORITY[a.method],
            _PATTERN_RANK.get(a.pattern, len(_PATTERN_RANK)),  # spikes before lasting changes
            -a.severity,
            a.student_id,
            a.subject or "",
            a.term or "",
        ),
    )


def detect_rule_based(
    data: pd.DataFrame,
    zscore_limit: float = DEFAULT_ZSCORE_LIMIT,
    jump_threshold: float = DEFAULT_JUMP_THRESHOLD,
) -> list[Anomaly]:
    """Run all three rule detectors on cleaned data (columns as produced by clean_rows)."""
    return rank_anomalies(
        detect_duplicate_ids(data)
        + detect_term_jumps(data, jump_threshold)
        + detect_zscore_outliers(data, zscore_limit)
    )


def detect_zscore_outliers(
    data: pd.DataFrame, limit: float = DEFAULT_ZSCORE_LIMIT
) -> list[Anomaly]:
    """Scores more than `limit` standard deviations from the mean of their subject and term.

    Uses the sample standard deviation. Groups with no spread are skipped. Note that a group
    of n scores can never reach |z| above (n - 1) / sqrt(n), so groups of 10 or fewer cannot
    produce a z-score above 3: tiny groups are never flagged, which is the honest outcome.
    """
    if data.empty:
        return []
    grouped = data.groupby(["subject", "term"])["score"]
    mean, std = grouped.transform("mean"), grouped.transform("std")
    z = (data["score"] - mean) / std
    # Degenerate groups (one score, or no spread) give NaN or infinite z: never flagged.
    # Filter z and mean with the same mask before assigning: assigning a full-length Series
    # to an empty selection would make pandas resurrect every row.
    mask = (z.abs() > limit) & z.notna() & (z.abs() != float("inf"))
    flagged = data[mask].assign(zvalue=z[mask], group_mean=mean[mask])
    return [
        Anomaly(
            method=AnomalyMethod.ZSCORE,
            student_id=row.student_id,
            student_name=row.student_name,
            subject=row.subject,
            term=row.term,
            score=float(row.score),
            severity=round(abs(float(row.zvalue)), 2),
            details=(
                f"{row.subject} {row.term}: score {row.score:g} is "
                f"{abs(row.zvalue):.1f} standard deviations "
                f"{'above' if row.zvalue > 0 else 'below'} the mean of {row.group_mean:.1f} "
                "for all students in that subject and term"
            ),
        )
        for row in flagged.itertuples()
    ]


def detect_term_jumps(
    data: pd.DataFrame, threshold: float = DEFAULT_JUMP_THRESHOLD
) -> list[Anomaly]:
    """Changes of more than `threshold` points between consecutive terms, per student and subject.

    Only consecutive terms count (T1 to T2, T2 to T3). If a term is missing the pair is
    skipped. If a student has several scores for one subject and term, their mean is used.
    A change of exactly `threshold` is not flagged.

    Each finding also says what happened next (its `pattern`): a spike that went back, a
    change that held, or a change in the latest term that cannot be judged yet. A genuine
    breakthrough (40, 75, 78) is "sustained"; a suspicious blip (34, 77, 38) is a "spike".
    """
    if data.empty:
        return []
    names = data.drop_duplicates("student_id").set_index("student_id")["student_name"]
    scores = data.pivot_table(
        index=["student_id", "subject"], columns="term", values="score", aggfunc="mean"
    ).reindex(columns=TERM_ORDER)

    anomalies: list[Anomaly] = []
    for earlier, later in zip(TERM_ORDER, TERM_ORDER[1:], strict=False):
        change = scores[later] - scores[earlier]  # NaN where a term is missing: never flagged
        for (student_id, subject), delta in change[change.abs() > threshold].items():
            series = scores.loc[(student_id, subject)]
            before, after = series[earlier], series[later]
            pattern, outlook = _classify_jump(series, earlier, later, threshold)
            anomalies.append(
                Anomaly(
                    method=AnomalyMethod.TERM_JUMP,
                    student_id=student_id,
                    student_name=names.get(student_id),
                    subject=subject,
                    term=later,
                    score=float(after),
                    severity=round(abs(float(delta)), 2),
                    pattern=pattern,
                    details=(
                        f"{subject}: score {'rose' if delta > 0 else 'fell'} from {before:g} "
                        f"({earlier}) to {after:g} ({later}), a change of {delta:+.1f} points; "
                        f"{outlook}"
                    ),
                )
            )
    return anomalies


def _classify_jump(
    series: pd.Series, earlier: str, later: str, threshold: float
) -> tuple[str, str]:
    """(pattern, sentence about what happened next) for one jump in a student's subject scores.

    `series` holds the scores for T1, T2 and T3 (NaN where missing). A jump is a spike if the
    score went back at least halfway to where it started.
    """
    t1, t2, t3 = (series[term] for term in TERM_ORDER)
    if (earlier, later) == ("T1", "T2"):
        if pd.isna(t3):
            return (
                PATTERN_LATEST,
                "T2 is the latest term, so it is not yet known whether this lasts",
            )
        if abs(t3 - t1) < abs(t2 - t1) / 2:
            return PATTERN_SPIKE, f"it then moved back to {t3:g} in T3, so this looks like a spike"
        return PATTERN_SUSTAINED, f"it did not go back in T3 ({t3:g})"

    # T2 to T3: is it the way back from an earlier jump?
    if not pd.isna(t1):
        earlier_jump = t2 - t1
        if (
            abs(earlier_jump) > threshold
            and (t3 - t2) * earlier_jump < 0
            and abs(t3 - t1) < abs(earlier_jump) / 2
        ):
            return PATTERN_SPIKE, "this reverses the earlier jump, so it looks like a spike"
    return PATTERN_LATEST, "T3 is the latest term, so it is not yet known whether this lasts"


def detect_duplicate_ids(data: pd.DataFrame) -> list[Anomaly]:
    """Student IDs that appear with more than one name (a typo or an ID collision)."""
    if data.empty:
        return []
    names_per_id = data.groupby("student_id")["student_name"].agg(lambda s: sorted(s.unique()))
    return [
        Anomaly(
            method=AnomalyMethod.DUPLICATE_ID,
            student_id=student_id,
            severity=float(len(names)),
            details=(
                f"student ID is used with {len(names)} different names: "
                + ", ".join(f"'{name}'" for name in names)
            ),
        )
        for student_id, names in names_per_id.items()
        if len(names) > 1
    ]
