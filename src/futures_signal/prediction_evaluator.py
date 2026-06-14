from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .storage import Storage, _direction_hit, _target_time


@dataclass(frozen=True)
class PredictionEvaluationResult:
    evaluated_at: datetime
    scanned: int
    labeled: int
    skipped_not_due: int
    skipped_missing_samples: int
    remaining_unlabeled: int


class PredictionEvaluationJob:
    def __init__(self, storage: Storage):
        self.storage = storage

    def run(self, until: datetime, limit: int = 500) -> PredictionEvaluationResult:
        scanned = 0
        labeled = 0
        skipped_not_due = 0
        skipped_missing_samples = 0

        for prediction in self.storage.list_unlabeled_predictions(limit=limit):
            scanned += 1
            target = _target_time(prediction.timestamp, prediction.horizon, self.storage.calendar)
            if target is None or target > until:
                skipped_not_due += 1
                continue

            future_return = self.storage.resolve_future_return_bp(prediction.payload, target)
            if future_return is None:
                skipped_missing_samples += 1
                continue

            self.storage.save_prediction_label(
                prediction_id=prediction.prediction_id,
                target=prediction.horizon,
                future_return_bp=future_return,
                direction_hit=_direction_hit(prediction.score, future_return),
                labeled_at=until,
                target_trading_day=target.date().isoformat(),
                calendar_source=self.storage.calendar_source,
            )
            labeled += 1

        return PredictionEvaluationResult(
            evaluated_at=until,
            scanned=scanned,
            labeled=labeled,
            skipped_not_due=skipped_not_due,
            skipped_missing_samples=skipped_missing_samples,
            remaining_unlabeled=self.storage.count_unlabeled_predictions(),
        )
