from __future__ import annotations

import json

from ..models import MarketAnalysis
from .base import SqliteRepository
from .shared import PendingPrediction, analysis_payload, dt, encode_json, prediction_horizons


class PredictionRepository(SqliteRepository):
    def enqueue_predictions(self, analysis: MarketAnalysis) -> None:
        payload = analysis_payload(analysis)
        with self._connect() as conn:
            for horizon in prediction_horizons(analysis.timestamp):
                conn.execute(
                    "insert or ignore into predictions (ts, horizon, score, band, payload_json) values (?, ?, ?, ?, ?)",
                    (
                        dt(analysis.timestamp),
                        horizon,
                        analysis.score,
                        analysis.band,
                        encode_json(payload),
                    ),
                )

    def list_unlabeled_predictions(self, limit: int = 500) -> list[PendingPrediction]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select p.id, p.ts, p.horizon, p.score, p.payload_json
                from predictions p
                where not exists (
                    select 1 from prediction_labels l where l.prediction_id = p.id
                )
                order by p.ts asc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [
            PendingPrediction(
                prediction_id=int(row["id"]),
                timestamp=json_datetime(row["ts"]),
                horizon=str(row["horizon"]),
                score=int(row["score"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def count_unlabeled_predictions(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                select count(*)
                from predictions p
                where not exists (
                    select 1 from prediction_labels l where l.prediction_id = p.id
                )
                """
            ).fetchone()
        return int(row[0]) if row is not None else 0


def json_datetime(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
