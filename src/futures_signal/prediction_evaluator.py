from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .repositories import PredictionLabelRepository, PredictionRepository
from .repositories.shared import direction_hit, target_time


@dataclass(frozen=True)
class PredictionEvaluationResult:
    evaluated_at: datetime
    scanned: int
    labeled: int
    skipped_not_due: int
    skipped_missing_samples: int
    remaining_unlabeled: int


class PredictionEvaluationJob:
    def __init__(
        self,
        predictions: PredictionRepository,
        prediction_labels: PredictionLabelRepository,
    ):
        self.predictions = predictions
        self.prediction_labels = prediction_labels

    def run(self, until: datetime, limit: int = 500) -> PredictionEvaluationResult:
        scanned = 0
        labeled = 0
        skipped_not_due = 0
        skipped_missing_samples = 0

        for prediction in self.predictions.list_unlabeled_predictions(limit=limit):
            scanned += 1
            target = target_time(prediction.timestamp, prediction.horizon, self.predictions.calendar)
            if target is None or target > until:
                skipped_not_due += 1
                continue

            future_return = self.prediction_labels.resolve_future_return_bp(prediction.payload, target)
            if future_return is None:
                skipped_missing_samples += 1
                continue

            self.prediction_labels.save_prediction_label(
                prediction_id=prediction.prediction_id,
                target=prediction.horizon,
                future_return_bp=future_return,
                direction_hit=direction_hit(prediction.score, future_return),
                labeled_at=until,
                target_trading_day=target.date().isoformat(),
                calendar_source=self.prediction_labels.calendar_source,
            )
            labeled += 1

        return PredictionEvaluationResult(
            evaluated_at=until,
            scanned=scanned,
            labeled=labeled,
            skipped_not_due=skipped_not_due,
            skipped_missing_samples=skipped_missing_samples,
            remaining_unlabeled=self.predictions.count_unlabeled_predictions(),
        )
