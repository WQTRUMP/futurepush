from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from ..models import HistoricalProductSnapshot
from .base import SqliteRepository
from .shared import dt


class MarketReadRepository(SqliteRepository):
    def get_reference_snapshot(
        self,
        product: str,
        now: datetime,
        lookback_minutes: int = 5,
        max_age_minutes: int = 30,
    ) -> HistoricalProductSnapshot | None:
        cutoff = now - timedelta(minutes=lookback_minutes)
        oldest = now - timedelta(minutes=max_age_minutes)
        with self._connect() as conn:
            row = conn.execute(
                """
                select ts, product, contract, futures_price, spot_price, basis_bp, volume, open_interest
                from snapshots
                where product = ? and ts <= ? and ts >= ?
                  and coalesce(valid_for_scoring, 1) = 1
                  and (substr(ts, 12, 8) between '09:30:00' and '11:30:59'
                       or substr(ts, 12, 8) between '13:00:00' and '15:00:59')
                order by ts desc
                limit 1
                """,
                (product, dt(cutoff), dt(oldest)),
            ).fetchone()
        return _historical_snapshot(row)

    def get_daily_reference_snapshot(self, product: str, now: datetime) -> HistoricalProductSnapshot | None:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        oldest = day_start - timedelta(days=14)
        with self._connect() as conn:
            row = self._reference_row(
                conn,
                product,
                day_start,
                oldest,
                "and substr(ts, 12, 8) between '14:55:00' and '15:00:59'",
            )
            if row is None:
                row = self._reference_row(
                    conn,
                    product,
                    day_start,
                    oldest,
                    "and (substr(ts, 12, 8) between '09:30:00' and '11:30:59' "
                    "or substr(ts, 12, 8) between '13:00:00' and '15:00:59')",
                )
        return _historical_snapshot(row)

    def _reference_row(
        self,
        conn: sqlite3.Connection,
        product: str,
        before: datetime,
        oldest: datetime,
        time_filter: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            f"""
            select ts, product, contract, futures_price, spot_price, basis_bp, volume, open_interest
            from snapshots
            where product = ? and ts < ? and ts >= ?
              and coalesce(valid_for_scoring, 1) = 1
              {time_filter}
            order by ts desc
            limit 1
            """,
            (product, dt(before), dt(oldest)),
        ).fetchone()

    def latest_contracts(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select product, contract
                from snapshots s
                where ts = (
                    select max(ts) from snapshots
                    where product = s.product and coalesce(valid_for_scoring, 1) = 1
                )
                and coalesce(valid_for_scoring, 1) = 1
                """
            ).fetchall()
        return {row["product"]: row["contract"] for row in rows}

    def latest_score(self) -> tuple[int | None, str | None]:
        with self._connect() as conn:
            row = conn.execute("select score, band from scores order by ts desc limit 1").fetchone()
        if row is None:
            return None, None
        return int(row["score"]), str(row["band"])

    def get_basis_history(self, product: str, now: datetime, days: int = 20) -> list[float]:
        oldest = now - timedelta(days=days)
        with self._connect() as conn:
            rows = conn.execute(
                """
                select basis_bp
                from snapshots
                where product = ? and ts >= ? and ts < ?
                  and coalesce(valid_for_scoring, 1) = 1
                  and substr(ts, 12, 5) = ?
                  and (substr(ts, 12, 8) between '09:30:00' and '11:30:59'
                       or substr(ts, 12, 8) between '13:00:00' and '15:00:59')
                order by ts asc
                """,
                (product, dt(oldest), dt(now), now.strftime("%H:%M")),
            ).fetchall()
        return [float(row["basis_bp"]) for row in rows]


def _historical_snapshot(row: sqlite3.Row | None) -> HistoricalProductSnapshot | None:
    if row is None:
        return None
    return HistoricalProductSnapshot(
        timestamp=datetime.fromisoformat(row["ts"]),
        product=row["product"],
        contract=row["contract"],
        futures_price=row["futures_price"],
        spot_price=row["spot_price"],
        basis_bp=row["basis_bp"],
        volume=row["volume"],
        open_interest=row["open_interest"],
    )
