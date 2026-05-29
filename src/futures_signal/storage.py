from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import HistoricalProductSnapshot, MarketAnalysis, ProductSignal


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists snapshots (
                    id integer primary key autoincrement,
                    ts text not null,
                    product text not null,
                    contract text not null,
                    futures_price real not null,
                    futures_change_pct real not null,
                    spot_price real not null,
                    spot_change_pct real not null,
                    basis real not null,
                    basis_bp real not null,
                    volume integer not null,
                    open_interest integer not null,
                    raw_json text not null
                );
                create index if not exists idx_snapshots_product_ts on snapshots(product, ts);

                create table if not exists scores (
                    id integer primary key autoincrement,
                    ts text not null,
                    score integer not null,
                    band text not null,
                    payload_json text not null
                );
                create index if not exists idx_scores_ts on scores(ts);

                create table if not exists alerts (
                    id integer primary key autoincrement,
                    ts text not null,
                    kind text not null,
                    band text not null,
                    score integer not null,
                    message text not null
                );
                create index if not exists idx_alerts_kind_ts on alerts(kind, ts);

                create table if not exists main_contract_changes (
                    id integer primary key autoincrement,
                    ts text not null,
                    product text not null,
                    old_contract text,
                    new_contract text not null
                );
                """
            )

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
                order by ts desc
                limit 1
                """,
                (product, _dt(cutoff), _dt(oldest)),
            ).fetchone()
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

    def get_daily_reference_snapshot(self, product: str, now: datetime) -> HistoricalProductSnapshot | None:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        oldest = day_start - timedelta(days=14)
        with self._connect() as conn:
            row = conn.execute(
                """
                select ts, product, contract, futures_price, spot_price, basis_bp, volume, open_interest
                from snapshots
                where product = ? and ts < ? and ts >= ?
                order by ts desc
                limit 1
                """,
                (product, _dt(day_start), _dt(oldest)),
            ).fetchone()
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

    def latest_contracts(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select product, contract
                from snapshots s
                where ts = (
                    select max(ts) from snapshots where product = s.product
                )
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
                order by ts asc
                """,
                (product, _dt(oldest), _dt(now)),
            ).fetchall()
        return [float(row["basis_bp"]) for row in rows]

    def save_analysis(self, analysis: MarketAnalysis) -> None:
        payload = _analysis_payload(analysis)
        with self._connect() as conn:
            for signal in analysis.signals.values():
                conn.execute(
                    """
                    insert into snapshots (
                        ts, product, contract, futures_price, futures_change_pct,
                        spot_price, spot_change_pct, basis, basis_bp, volume,
                        open_interest, raw_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _dt(analysis.timestamp),
                        signal.product,
                        signal.contract,
                        signal.futures_price,
                        signal.futures_change_pct,
                        signal.spot_price,
                        signal.spot_change_pct,
                        signal.basis,
                        signal.basis_bp,
                        signal.volume,
                        signal.open_interest,
                        json.dumps(_signal_payload(signal), ensure_ascii=False),
                    ),
                )
                if signal.main_contract_changed:
                    conn.execute(
                        """
                        insert into main_contract_changes (ts, product, old_contract, new_contract)
                        values (?, ?, ?, ?)
                        """,
                        (
                            _dt(analysis.timestamp),
                            signal.product,
                            signal.previous_contract,
                            signal.contract,
                        ),
                    )
            conn.execute(
                "insert into scores (ts, score, band, payload_json) values (?, ?, ?, ?)",
                (_dt(analysis.timestamp), analysis.score, analysis.band, json.dumps(payload, ensure_ascii=False)),
            )

    def has_recent_alert(self, kind: str, now: datetime, cooldown_seconds: int) -> bool:
        cutoff = now - timedelta(seconds=cooldown_seconds)
        with self._connect() as conn:
            row = conn.execute(
                "select id from alerts where kind = ? and ts >= ? order by ts desc limit 1",
                (kind, _dt(cutoff)),
            ).fetchone()
        return row is not None

    def save_alert(self, now: datetime, kind: str, band: str, score: int, message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into alerts (ts, kind, band, score, message) values (?, ?, ?, ?, ?)",
                (_dt(now), kind, band, score, message),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _dt(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _signal_payload(signal: ProductSignal) -> dict[str, Any]:
    return {
        "product": signal.product,
        "contract": signal.contract,
        "previous_contract": signal.previous_contract,
        "futures_price": signal.futures_price,
        "futures_change_pct": signal.futures_change_pct,
        "spot_price": signal.spot_price,
        "spot_change_pct": signal.spot_change_pct,
        "basis": signal.basis,
        "basis_bp": signal.basis_bp,
        "basis_state": signal.basis_state,
        "basis_change_bp": signal.basis_change_bp,
        "basis_change_label": signal.basis_change_label,
        "basis_percentile": signal.basis_percentile,
        "basis_zscore": signal.basis_zscore,
        "basis_history_count": signal.basis_history_count,
        "futures_minus_spot_pct": signal.futures_minus_spot_pct,
        "volume": signal.volume,
        "volume_change": signal.volume_change,
        "open_interest": signal.open_interest,
        "open_interest_change": signal.open_interest_change,
        "price_oi_signal": signal.price_oi_signal,
        "main_contract_changed": signal.main_contract_changed,
        "daily_price_change": signal.daily_price_change,
        "daily_open_interest_change": signal.daily_open_interest_change,
        "daily_basis_change_bp": signal.daily_basis_change_bp,
    }


def _analysis_payload(analysis: MarketAnalysis) -> dict[str, Any]:
    return {
        "timestamp": _dt(analysis.timestamp),
        "score": analysis.score,
        "band": analysis.band,
        "previous_score": analysis.previous_score,
        "previous_band": analysis.previous_band,
        "components": analysis.components,
        "signals": {product: _signal_payload(signal) for product, signal in analysis.signals.items()},
        "reasons": analysis.reasons,
        "warnings": analysis.warnings,
        "alert_kind": analysis.alert_kind,
        "term_summary": analysis.term_summary,
    }
