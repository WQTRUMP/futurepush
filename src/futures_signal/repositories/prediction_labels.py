from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .base import SqliteRepository
from .shared import dt


class PredictionLabelRepository(SqliteRepository):
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
        with self._connect() as conn:
            conn.execute(
                """
                insert or ignore into prediction_labels (
                    prediction_id, target, future_return_bp, direction_hit, labeled_at,
                    target_trading_day, calendar_source
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prediction_id,
                    target,
                    future_return_bp,
                    1 if direction_hit else 0,
                    dt(labeled_at),
                    target_trading_day,
                    calendar_source,
                ),
            )

    def resolve_future_return_bp(
        self,
        payload: dict[str, Any],
        target: datetime,
        window_minutes: int = 30,
    ) -> float | None:
        with self._connect() as conn:
            return self._future_return_bp(conn, payload, target, window_minutes)

    def _future_return_bp(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
        target: datetime,
        window_minutes: int = 30,
    ) -> float | None:
        signals = payload.get("signals", {})
        if not isinstance(signals, dict):
            return None
        returns = []
        for product, signal in signals.items():
            if not isinstance(signal, dict):
                continue
            start_price = signal.get("spot_price")
            if not start_price:
                continue
            target_price = self._nearest_spot_price(conn, str(product), target, window_minutes)
            if target_price is None:
                continue
            returns.append((float(target_price) / float(start_price) - 1) * 10000)
        if not returns:
            return None
        return sum(returns) / len(returns)

    def _nearest_spot_price(
        self,
        conn: sqlite3.Connection,
        product: str,
        target: datetime,
        window_minutes: int,
    ) -> float | None:
        start = target - timedelta(minutes=window_minutes)
        end = target + timedelta(minutes=window_minutes)
        rows = conn.execute(
            """
            select ts, spot_price
            from snapshots
            where product = ? and ts >= ? and ts <= ?
              and coalesce(valid_for_scoring, 1) = 1
            order by ts asc
            """,
            (product, dt(start), dt(end)),
        ).fetchall()
        if not rows:
            return None
        nearest = min(rows, key=lambda row: abs((datetime.fromisoformat(row["ts"]) - target).total_seconds()))
        return float(nearest["spot_price"])
