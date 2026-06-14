from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from .market_calendar import TradingCalendar
from .models import HistoricalProductSnapshot, MarketAnalysis, ProductSignal

MORNING_PREDICTION_CUTOFF = time(10, 0)
TAIL_PREDICTION_START = time(14, 30)
PREDICTION_DAY_END = time(15, 5)


class Storage:
    def __init__(self, db_path: Path, calendar: TradingCalendar | None = None):
        self.db_path = db_path
        self.calendar = calendar

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
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
            self._ensure_snapshot_columns(conn)
            self._ensure_prediction_label_columns(conn)
            conn.execute(
                """
                create index if not exists idx_snapshots_product_valid_ts
                on snapshots(product, valid_for_scoring, ts)
                """
            )

    def _ensure_snapshot_columns(self, conn: sqlite3.Connection) -> None:
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

    def _ensure_prediction_label_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("pragma table_info(prediction_labels)").fetchall()}
        migrations = {
            "target_trading_day": "alter table prediction_labels add column target_trading_day text",
            "calendar_source": "alter table prediction_labels add column calendar_source text",
        }
        for column, statement in migrations.items():
            if column not in existing:
                conn.execute(statement)

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
            row = self._reference_snapshot_row(conn, product, cutoff, oldest)
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

    def _reference_snapshot_row(
        self,
        conn: sqlite3.Connection,
        product: str,
        cutoff: datetime,
        oldest: datetime,
    ) -> sqlite3.Row | None:
        return conn.execute(
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
            (product, _dt(cutoff), _dt(oldest)),
        ).fetchone()

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
            (product, _dt(before), _dt(oldest)),
        ).fetchone()

    def latest_contracts(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = self._latest_contract_rows(conn)
        return {row["product"]: row["contract"] for row in rows}

    def _latest_contract_rows(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute(
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

    def latest_score(self) -> tuple[int | None, str | None]:
        with self._connect() as conn:
            row = self._latest_score_row(conn)
        if row is None:
            return None, None
        return int(row["score"]), str(row["band"])

    def _latest_score_row(self, conn: sqlite3.Connection) -> sqlite3.Row | None:
        return conn.execute("select score, band from scores order by ts desc limit 1").fetchone()

    def get_basis_history(self, product: str, now: datetime, days: int = 20) -> list[float]:
        oldest = now - timedelta(days=days)
        with self._connect() as conn:
            rows = self._basis_history_rows(conn, product, oldest, now)
        return [float(row["basis_bp"]) for row in rows]

    def _basis_history_rows(
        self,
        conn: sqlite3.Connection,
        product: str,
        oldest: datetime,
        now: datetime,
    ) -> list[sqlite3.Row]:
        return conn.execute(
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
            (product, _dt(oldest), _dt(now), now.strftime("%H:%M")),
        ).fetchall()

    def load_analysis_inputs(
        self,
        products: tuple[str, ...],
        now: datetime,
        basis_history_days: int,
    ) -> tuple[
        dict[str, HistoricalProductSnapshot | None],
        dict[str, HistoricalProductSnapshot | None],
        dict[str, list[float]],
        dict[str, str],
        int | None,
        str | None,
    ]:
        references: dict[str, HistoricalProductSnapshot | None] = {}
        daily_references: dict[str, HistoricalProductSnapshot | None] = {}
        basis_histories: dict[str, list[float]] = {}
        cutoff = now - timedelta(minutes=5)
        oldest = now - timedelta(minutes=30)
        basis_oldest = now - timedelta(days=basis_history_days)
        with self._connect() as conn:
            for product in products:
                row = self._reference_snapshot_row(conn, product, cutoff, oldest)
                references[product] = None if row is None else HistoricalProductSnapshot(
                    timestamp=datetime.fromisoformat(row["ts"]),
                    product=row["product"],
                    contract=row["contract"],
                    futures_price=row["futures_price"],
                    spot_price=row["spot_price"],
                    basis_bp=row["basis_bp"],
                    volume=row["volume"],
                    open_interest=row["open_interest"],
                )
                daily_references[product] = self._daily_reference_snapshot(conn, product, now)
                basis_rows = self._basis_history_rows(conn, product, basis_oldest, now)
                basis_histories[product] = [float(item["basis_bp"]) for item in basis_rows]
            latest_contracts = {row["product"]: row["contract"] for row in self._latest_contract_rows(conn)}
            score_row = self._latest_score_row(conn)
        previous_score = None if score_row is None else int(score_row["score"])
        previous_band = None if score_row is None else str(score_row["band"])
        return references, daily_references, basis_histories, latest_contracts, previous_score, previous_band

    def _daily_reference_snapshot(
        self,
        conn: sqlite3.Connection,
        product: str,
        now: datetime,
    ) -> HistoricalProductSnapshot | None:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        oldest = day_start - timedelta(days=14)
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

    def save_analysis(self, analysis: MarketAnalysis) -> None:
        payload = _analysis_payload(analysis)
        with self._connect() as conn:
            for signal in analysis.signals.values():
                conn.execute(
                    """
                    insert or ignore into snapshots (
                        ts, fetched_at, market_ts, product, contract, futures_price, futures_change_pct,
                        spot_price, spot_change_pct, basis, basis_bp, volume,
                        open_interest, is_stale, source, valid_for_scoring, raw_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _dt(analysis.timestamp),
                        _dt(analysis.fetched_at or analysis.timestamp),
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
                        0 if analysis.valid_for_scoring else 1,
                        analysis.source,
                        1 if analysis.valid_for_scoring else 0,
                        json.dumps(_signal_payload(signal), ensure_ascii=False),
                    ),
                )
                if signal.main_contract_changed:
                    conn.execute(
                        """
                        insert or ignore into main_contract_changes (ts, product, old_contract, new_contract)
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
                "insert or ignore into scores (ts, score, band, payload_json) values (?, ?, ?, ?)",
                (_dt(analysis.timestamp), analysis.score, analysis.band, json.dumps(payload, ensure_ascii=False)),
            )
            for horizon in _prediction_horizons(analysis.timestamp):
                conn.execute(
                    "insert or ignore into predictions (ts, horizon, score, band, payload_json) values (?, ?, ?, ?, ?)",
                    (
                        _dt(analysis.timestamp),
                        horizon,
                        analysis.score,
                        analysis.band,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )

    def label_due_predictions(self, now: datetime) -> int:
        labeled = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                select p.id, p.ts, p.horizon, p.score, p.payload_json
                from predictions p
                where not exists (
                    select 1 from prediction_labels l where l.prediction_id = p.id
                )
                order by p.ts asc
                limit 500
                """
            ).fetchall()
            for row in rows:
                pred_ts = datetime.fromisoformat(row["ts"])
                target = _target_time(pred_ts, row["horizon"], self.calendar)
                if target is None or target > now:
                    continue
                payload = json.loads(row["payload_json"])
                future_return = self._future_return_bp(conn, payload, target)
                if future_return is None:
                    continue
                hit = _direction_hit(int(row["score"]), future_return)
                conn.execute(
                    """
                    insert or ignore into prediction_labels (
                        prediction_id, target, future_return_bp, direction_hit, labeled_at,
                        target_trading_day, calendar_source
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["horizon"],
                        future_return,
                        1 if hit else 0,
                        _dt(now),
                        target.date().isoformat(),
                        self.calendar.source if self.calendar is not None else "weekday",
                    ),
                )
                labeled += 1
        return labeled

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
            (product, _dt(start), _dt(end)),
        ).fetchall()
        if not rows:
            return None
        nearest = min(rows, key=lambda row: abs((datetime.fromisoformat(row["ts"]) - target).total_seconds()))
        return float(nearest["spot_price"])

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
                (_dt(now), kind, band, score, _alert_record(message)),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            self.db_path.chmod(0o600)
        except FileNotFoundError:
            pass
        return conn


def _dt(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _prediction_horizons(now: datetime) -> list[str]:
    current = now.time()
    if current < MORNING_PREDICTION_CUTOFF:
        return ["same_day_1030", "same_day_1130", "same_day_close"]
    if current < TAIL_PREDICTION_START:
        return ["same_day_1130", "same_day_close"]
    if current <= PREDICTION_DAY_END:
        return ["next_day_open", "next_day_1030", "next_day_close"]
    return []


def _target_time(
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
    target_day = _next_trading_day(pred_ts, calendar)
    return target_day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _next_trading_day(value: datetime, calendar: TradingCalendar | None = None) -> datetime:
    if calendar is not None:
        next_day = calendar.next_trading_day(value.date())
        return datetime.combine(next_day, value.timetz()).replace(tzinfo=value.tzinfo)
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _direction_hit(score: int, future_return_bp: float) -> bool:
    if score >= 60:
        return future_return_bp > 0
    if score <= 39:
        return future_return_bp < 0
    return abs(future_return_bp) <= 20


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


def _analysis_payload(analysis: MarketAnalysis) -> dict[str, Any]:
    return {
        "timestamp": _dt(analysis.timestamp),
        "fetched_at": _dt(analysis.fetched_at or analysis.timestamp),
        "source": analysis.source,
        "valid_for_scoring": analysis.valid_for_scoring,
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
        "position_trends": {
            product: {
                "days": trend.days,
                "net_short_change_sum": trend.net_short_change_sum,
                "latest_net_short_change": trend.latest_net_short_change,
            }
            for product, trend in analysis.position_trends.items()
        },
    }


def _alert_record(message: str, preview_chars: int = 160) -> str:
    normalized = " ".join(message.split())
    preview = normalized[:preview_chars]
    digest = sha256(message.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest} preview:{preview}"
