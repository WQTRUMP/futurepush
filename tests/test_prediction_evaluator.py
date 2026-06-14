from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from futures_signal.market_calendar import TradingCalendar
from futures_signal.prediction_evaluator import PredictionEvaluationJob
from futures_signal.storage import Storage


TZ = ZoneInfo("Asia/Shanghai")


def test_evaluation_job_uses_next_trading_day_calendar(tmp_path: Path):
    calendar = TradingCalendar(
        TZ,
        use_akshare=True,
        fetcher=lambda: ["2026-05-29", "2026-06-02"],
    )
    storage = Storage(tmp_path / "market.db", calendar=calendar)
    storage.init()

    with storage._connect() as conn:
        conn.execute(
            """
            insert into predictions (ts, horizon, score, band, payload_json)
            values (?, ?, ?, ?, ?)
            """,
            (
                "2026-05-29T14:50:00+08:00",
                "next_day_open",
                70,
                "偏多但不强",
                '{"signals":{"IF":{"spot_price":4000}}}',
            ),
        )
        conn.execute(
            """
            insert into snapshots (
                ts, product, contract, futures_price, futures_change_pct,
                spot_price, spot_change_pct, basis, basis_bp, volume,
                open_interest, valid_for_scoring, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-06-02T09:30:00+08:00", "IF", "IF2606", 4010, 0.1, 4040, 0.2, 0, 0, 1, 1, 1, "{}"),
        )

    result = PredictionEvaluationJob(storage.predictions, storage.prediction_labels).run(
        datetime(2026, 6, 2, 10, 0, tzinfo=TZ)
    )

    assert result.labeled == 1
    assert result.scanned == 1
    with storage._connect() as conn:
        row = conn.execute(
            """
            select target_trading_day, calendar_source
            from prediction_labels
            """
        ).fetchone()
    assert row["target_trading_day"] == "2026-06-02"
    assert row["calendar_source"] == "akshare"


def test_evaluation_job_skips_invalid_signals_and_missing_prices(tmp_path: Path):
    storage = Storage(tmp_path / "market.db")
    storage.init()

    with storage._connect() as conn:
        conn.execute(
            """
            insert into predictions (ts, horizon, score, band, payload_json)
            values (?, ?, ?, ?, ?)
            """,
            ("2026-06-02T09:20:00+08:00", "same_day_1030", 50, "中性", '{"signals": "bad"}'),
        )
        conn.execute(
            """
            insert into predictions (ts, horizon, score, band, payload_json)
            values (?, ?, ?, ?, ?)
            """,
            ("2026-06-02T09:21:00+08:00", "same_day_1030", 50, "中性", '{"signals": {"IF": {}}}'),
        )

    result = PredictionEvaluationJob(storage.predictions, storage.prediction_labels).run(
        datetime(2026, 6, 2, 10, 30, tzinfo=TZ)
    )

    assert result.scanned == 2
    assert result.labeled == 0
    assert result.skipped_missing_samples == 2
    assert result.remaining_unlabeled == 2


def test_evaluation_job_respects_limit_and_due_cutoff(tmp_path: Path):
    storage = Storage(tmp_path / "market.db")
    storage.init()

    with storage._connect() as conn:
        conn.execute(
            """
            insert into predictions (ts, horizon, score, band, payload_json)
            values (?, ?, ?, ?, ?)
            """,
            ("2026-06-02T09:20:00+08:00", "same_day_1030", 65, "偏多", '{"signals":{"IF":{"spot_price":4000}}}'),
        )
        conn.execute(
            """
            insert into predictions (ts, horizon, score, band, payload_json)
            values (?, ?, ?, ?, ?)
            """,
            ("2026-06-02T14:40:00+08:00", "next_day_open", 65, "偏多", '{"signals":{"IF":{"spot_price":4010}}}'),
        )
        conn.execute(
            """
            insert into snapshots (
                ts, product, contract, futures_price, futures_change_pct,
                spot_price, spot_change_pct, basis, basis_bp, volume,
                open_interest, valid_for_scoring, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-06-02T10:30:00+08:00", "IF", "IF2606", 4015, 0.1, 4040, 0.2, 0, 0, 1, 1, 1, "{}"),
        )

    result = PredictionEvaluationJob(storage.predictions, storage.prediction_labels).run(
        datetime(2026, 6, 2, 10, 30, tzinfo=TZ),
        limit=1,
    )

    assert result.scanned == 1
    assert result.labeled == 1
    assert result.skipped_not_due == 0
    assert result.remaining_unlabeled == 1


def test_evaluation_job_skips_predictions_that_are_not_due_yet(tmp_path: Path):
    storage = Storage(tmp_path / "market.db")
    storage.init()

    with storage._connect() as conn:
        conn.execute(
            """
            insert into predictions (ts, horizon, score, band, payload_json)
            values (?, ?, ?, ?, ?)
            """,
            ("2026-06-02T14:40:00+08:00", "next_day_open", 65, "偏多", '{"signals":{"IF":{"spot_price":4010}}}'),
        )

    result = PredictionEvaluationJob(storage.predictions, storage.prediction_labels).run(
        datetime(2026, 6, 2, 14, 50, tzinfo=TZ)
    )

    assert result.scanned == 1
    assert result.labeled == 0
    assert result.skipped_not_due == 1
    assert result.remaining_unlabeled == 1
