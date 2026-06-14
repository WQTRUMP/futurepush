from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .market_calendar import TradingCalendar
from .models import HistoricalProductSnapshot, MarketAnalysis
from .prediction_evaluator import PredictionEvaluationJob
from .repositories import (
    AnalysisWriteRepository,
    MarketReadRepository,
    PendingPrediction,
    PredictionLabelRepository,
    PredictionRepository,
)
from .repositories.shared import (
    MORNING_PREDICTION_CUTOFF,
    PREDICTION_DAY_END,
    TAIL_PREDICTION_START,
    alert_record as _alert_record,
    analysis_payload as _analysis_payload,
    connect_sqlite,
    direction_hit as _direction_hit,
    dt as _dt,
    next_trading_day as _next_trading_day,
    prediction_horizons as _prediction_horizons,
    signal_payload as _signal_payload,
    target_time as _target_time,
)


class Storage:
    def __init__(self, db_path: Path, calendar: TradingCalendar | None = None):
        self.db_path = db_path
        self.calendar = calendar
        self.market_reads = MarketReadRepository(db_path, calendar=calendar)
        self.analysis_writes = AnalysisWriteRepository(db_path, calendar=calendar)
        self.predictions = PredictionRepository(db_path, calendar=calendar)
        self.prediction_labels = PredictionLabelRepository(db_path, calendar=calendar)

    def init(self) -> None:
        self.market_reads.init()

    def get_reference_snapshot(
        self,
        product: str,
        now: datetime,
        lookback_minutes: int = 5,
        max_age_minutes: int = 30,
    ) -> HistoricalProductSnapshot | None:
        return self.market_reads.get_reference_snapshot(product, now, lookback_minutes, max_age_minutes)

    def get_daily_reference_snapshot(self, product: str, now: datetime) -> HistoricalProductSnapshot | None:
        return self.market_reads.get_daily_reference_snapshot(product, now)

    def latest_contracts(self) -> dict[str, str]:
        return self.market_reads.latest_contracts()

    def latest_score(self) -> tuple[int | None, str | None]:
        return self.market_reads.latest_score()

    def get_basis_history(self, product: str, now: datetime, days: int = 20) -> list[float]:
        return self.market_reads.get_basis_history(product, now, days)

    def save_analysis(self, analysis: MarketAnalysis) -> None:
        self.analysis_writes.save_analysis(analysis)
        self.predictions.enqueue_predictions(analysis)

    @property
    def calendar_source(self) -> str:
        return self.predictions.calendar_source

    def list_unlabeled_predictions(self, limit: int = 500) -> list[PendingPrediction]:
        return self.predictions.list_unlabeled_predictions(limit=limit)

    def count_unlabeled_predictions(self) -> int:
        return self.predictions.count_unlabeled_predictions()

    def save_prediction_label(
        self,
        *,
        prediction_id: int,
        target: str,
        future_return_bp: float,
        direction_hit: bool,
        labeled_at: datetime,
        target_trading_day: str,
        calendar_source: str,
    ) -> None:
        self.prediction_labels.save_prediction_label(
            prediction_id=prediction_id,
            target=target,
            future_return_bp=future_return_bp,
            direction_hit=direction_hit,
            labeled_at=labeled_at,
            target_trading_day=target_trading_day,
            calendar_source=calendar_source,
        )

    def label_due_predictions(self, now: datetime, limit: int = 500) -> int:
        return PredictionEvaluationJob(self.predictions, self.prediction_labels).run(now, limit=limit).labeled

    def resolve_future_return_bp(
        self,
        payload: dict[str, Any],
        target: datetime,
        window_minutes: int = 30,
    ) -> float | None:
        return self.prediction_labels.resolve_future_return_bp(payload, target, window_minutes)

    def _future_return_bp(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
        target: datetime,
        window_minutes: int = 30,
    ) -> float | None:
        return self.prediction_labels._future_return_bp(conn, payload, target, window_minutes)

    def _nearest_spot_price(
        self,
        conn: sqlite3.Connection,
        product: str,
        target: datetime,
        window_minutes: int,
    ) -> float | None:
        return self.prediction_labels._nearest_spot_price(conn, product, target, window_minutes)

    def has_recent_alert(self, kind: str, now: datetime, cooldown_seconds: int) -> bool:
        return self.analysis_writes.has_recent_alert(kind, now, cooldown_seconds)

    def save_alert(self, now: datetime, kind: str, band: str, score: int, message: str) -> None:
        self.analysis_writes.save_alert(now, kind, band, score, message)

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.db_path)
