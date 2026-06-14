from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..market_calendar import TradingCalendar
from ..models import MarketAnalysis, ProductSignal

MORNING_PREDICTION_CUTOFF = time(10, 0)
TAIL_PREDICTION_START = time(14, 30)
PREDICTION_DAY_END = time(15, 5)


@dataclass(frozen=True)
class PendingPrediction:
    prediction_id: int
    timestamp: datetime
    horizon: str
    score: int
    payload: dict[str, Any]


def initialize_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_sqlite(db_path) as conn:
        conn.executescript(
            """
            create table if not exists snapshots (
                id integer primary key autoincrement,
                ts text not null,
                fetched_at text,
                market_ts text,
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
                is_stale integer default 0,
                source text default 'unknown',
                valid_for_scoring integer default 1,
                raw_json text not null
            );
            create index if not exists idx_snapshots_product_ts on snapshots(product, ts);
            create unique index if not exists uq_snapshots_product_ts_contract
            on snapshots(product, ts, contract);

            create table if not exists scores (
                id integer primary key autoincrement,
                ts text not null,
                score integer not null,
                band text not null,
                payload_json text not null
            );
            create index if not exists idx_scores_ts on scores(ts);
            create unique index if not exists uq_scores_ts on scores(ts);

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

            create table if not exists predictions (
                id integer primary key autoincrement,
                ts text not null,
                horizon text not null,
                score integer not null,
                band text not null,
                payload_json text not null
            );
            create index if not exists idx_predictions_ts on predictions(ts);
            create unique index if not exists uq_predictions_ts_horizon on predictions(ts, horizon);

            create table if not exists prediction_labels (
                prediction_id integer not null,
                target text not null,
                future_return_bp real not null,
                direction_hit integer not null,
                labeled_at text not null,
                primary key (prediction_id, target)
            );
            """
        )
        ensure_snapshot_columns(conn)
        ensure_prediction_label_columns(conn)
        conn.execute(
            """
            create index if not exists idx_snapshots_product_valid_ts
            on snapshots(product, valid_for_scoring, ts)
            """
        )


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        db_path.chmod(0o600)
    except FileNotFoundError:
        pass
    return conn


def ensure_snapshot_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(snapshots)").fetchall()}
    migrations = {
        "fetched_at": "alter table snapshots add column fetched_at text",
        "market_ts": "alter table snapshots add column market_ts text",
        "is_stale": "alter table snapshots add column is_stale integer default 0",
        "source": "alter table snapshots add column source text default 'unknown'",
        "valid_for_scoring": "alter table snapshots add column valid_for_scoring integer default 1",
    }
    for column, statement in migrations.items():
        if column not in existing:
            conn.execute(statement)


def ensure_prediction_label_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(prediction_labels)").fetchall()}
    migrations = {
        "target_trading_day": "alter table prediction_labels add column target_trading_day text",
        "calendar_source": "alter table prediction_labels add column calendar_source text",
    }
    for column, statement in migrations.items():
        if column not in existing:
            conn.execute(statement)


def dt(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def prediction_horizons(now: datetime) -> list[str]:
    current = now.time()
    if current < MORNING_PREDICTION_CUTOFF:
        return ["same_day_1030", "same_day_1130", "same_day_close"]
    if current < TAIL_PREDICTION_START:
        return ["same_day_1130", "same_day_close"]
    if current <= PREDICTION_DAY_END:
        return ["next_day_open", "next_day_1030", "next_day_close"]
    return []


def target_time(
    pred_ts: datetime,
    horizon: str,
    calendar: TradingCalendar | None = None,
) -> datetime | None:
    same_day_targets = {
        "same_day_1030": (10, 30),
        "same_day_1130": (11, 30),
        "same_day_close": (15, 0),
    }
    if horizon in same_day_targets:
        hour, minute = same_day_targets[horizon]
        return pred_ts.replace(hour=hour, minute=minute, second=0, microsecond=0)

    next_day_targets = {
        "next_day_open": (9, 30),
        "next_day_1030": (10, 30),
        "next_day_close": (15, 0),
    }
    if horizon not in next_day_targets:
        return None
    hour, minute = next_day_targets[horizon]
    target_day = next_trading_day(pred_ts, calendar)
    return target_day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def next_trading_day(value: datetime, calendar: TradingCalendar | None = None) -> datetime:
    if calendar is not None:
        next_day = calendar.next_trading_day(value.date())
        return datetime.combine(next_day, value.timetz()).replace(tzinfo=value.tzinfo)
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def direction_hit(score: int, future_return_bp: float) -> bool:
    if score >= 60:
        return future_return_bp > 0
    if score <= 39:
        return future_return_bp < 0
    return abs(future_return_bp) <= 20


def signal_payload(signal: ProductSignal) -> dict[str, Any]:
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
        "lead_beta": signal.lead_beta,
        "futures_return_5m_pct": signal.futures_return_5m_pct,
        "spot_return_5m_pct": signal.spot_return_5m_pct,
        "lead_residual_5m_pct": signal.lead_residual_5m_pct,
        "volume": signal.volume,
        "volume_change": signal.volume_change,
        "volume_change_ratio": signal.volume_change_ratio,
        "open_interest": signal.open_interest,
        "open_interest_change": signal.open_interest_change,
        "open_interest_change_ratio": signal.open_interest_change_ratio,
        "price_change_5m": signal.price_change_5m,
        "price_oi_signal": signal.price_oi_signal,
        "main_contract_changed": signal.main_contract_changed,
        "daily_price_change": signal.daily_price_change,
        "daily_open_interest_change": signal.daily_open_interest_change,
        "daily_open_interest_change_ratio": signal.daily_open_interest_change_ratio,
        "daily_basis_change_bp": signal.daily_basis_change_bp,
        "net_short_change_top20": signal.net_short_change_top20,
        "net_short_change_top20_ratio": signal.net_short_change_top20_ratio,
        "citic_net_short_change": signal.citic_net_short_change,
        "citic_net_short_change_ratio": signal.citic_net_short_change_ratio,
        "position_rank_lag_days": signal.position_rank_lag_days,
        "position_rank_is_fallback": signal.position_rank_is_fallback,
    }


def analysis_payload(analysis: MarketAnalysis) -> dict[str, Any]:
    return {
        "timestamp": dt(analysis.timestamp),
        "fetched_at": dt(analysis.fetched_at or analysis.timestamp),
        "source": analysis.source,
        "valid_for_scoring": analysis.valid_for_scoring,
        "score": analysis.score,
        "band": analysis.band,
        "previous_score": analysis.previous_score,
        "previous_band": analysis.previous_band,
        "components": analysis.components,
        "signals": {product: signal_payload(signal) for product, signal in analysis.signals.items()},
        "reasons": analysis.reasons,
        "warnings": analysis.warnings,
        "alert_kind": analysis.alert_kind,
        "term_summary": analysis.term_summary,
        "position_trends": {
            product: {
                "days": trend.days,
                "net_short_change_sum": trend.net_short_change_sum,
                "latest_net_short_change": trend.latest_net_short_change,
            }
            for product, trend in analysis.position_trends.items()
        },
    }


def alert_record(message: str, preview_chars: int = 160) -> str:
    normalized = " ".join(message.split())
    preview = normalized[:preview_chars]
    digest = sha256(message.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest} preview:{preview}"


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
