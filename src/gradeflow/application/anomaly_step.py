"""The Anomalies workflow step.

Kept in its own module on purpose: it imports scikit-learn (through the student outlier
detector), which only the container-image Lambda has. The zip Lambdas import
`pipeline_steps`, which must never import this module.
"""

from collections import Counter

from gradeflow.application.pipeline_state import AnomalyOutcome, PipelineState, require
from gradeflow.application.ports import DatasetArtifacts
from gradeflow.domain.anomalies import detect_rule_based, rank_anomalies
from gradeflow.domain.student_outliers import detect_student_outliers


class DetectAnomaliesStep:
    """Run the rule-based detectors and the Isolation Forest on the cleaned data.

    All findings go to S3 (ranked, most important first); the state only gets the counts.
    """

    def __init__(
        self,
        artifacts: DatasetArtifacts,
        zscore_limit: float,
        jump_threshold: float,
        contamination: float,
    ) -> None:
        self._artifacts = artifacts
        self._zscore_limit = zscore_limit
        self._jump_threshold = jump_threshold
        self._contamination = contamination

    def execute(self, state: PipelineState) -> AnomalyOutcome:
        cleaning = require(state.cleaning, "cleaning")
        data = self._artifacts.load_clean_data(cleaning.processed_key)

        rules = detect_rule_based(data, self._zscore_limit, self._jump_threshold)
        forest = detect_student_outliers(data, self._contamination)
        anomalies = rank_anomalies(rules + forest.anomalies)

        return AnomalyOutcome(
            total=len(anomalies),
            by_method=dict(Counter(a.method.value for a in anomalies)),
            anomalies_key=self._artifacts.save_anomalies(state.dataset_id, anomalies),
            ml_skipped=forest.skipped,
        )
