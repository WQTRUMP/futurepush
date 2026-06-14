from __future__ import annotations

from datetime import datetime, timedelta

from ..models import MarketAnalysis
from .base import SqliteRepository
from .shared import alert_record, analysis_payload, dt, encode_json


class AnalysisWriteRepository(SqliteRepository):
    def save_analysis(self, analysis: MarketAnalysis) -> None:
        payload = analysis_payload(analysis)
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
                        dt(analysis.timestamp),
                        dt(analysis.fetched_at or analysis.timestamp),
                        dt(analysis.timestamp),
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
                        encode_json(payload["signals"][signal.product]),
                    ),
                )
                if signal.main_contract_changed:
                    conn.execute(
                        """
                        insert or ignore into main_contract_changes (ts, product, old_contract, new_contract)
                        values (?, ?, ?, ?)
                        """,
                        (
                            dt(analysis.timestamp),
                            signal.product,
                            signal.previous_contract,
                            signal.contract,
                        ),
                    )
            conn.execute(
                "insert or ignore into scores (ts, score, band, payload_json) values (?, ?, ?, ?)",
                (dt(analysis.timestamp), analysis.score, analysis.band, encode_json(payload)),
            )

    def has_recent_alert(self, kind: str, now: datetime, cooldown_seconds: int) -> bool:
        cutoff = now - timedelta(seconds=cooldown_seconds)
        with self._connect() as conn:
            row = conn.execute(
                "select id from alerts where kind = ? and ts >= ? order by ts desc limit 1",
                (kind, dt(cutoff)),
            ).fetchone()
        return row is not None

    def save_alert(self, now: datetime, kind: str, band: str, score: int, message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into alerts (ts, kind, band, score, message) values (?, ?, ?, ?, ?)",
                (dt(now), kind, band, score, alert_record(message)),
            )
