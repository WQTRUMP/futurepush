from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from futures_signal.market_calendar import TradingCalendar
from futures_signal.storage import Storage


TZ = ZoneInfo("Asia/Shanghai")


def test_daily_reference_prefers_previous_tail_valid_session(tmp_path: Path):
    storage = Storage(tmp_path / "market.db")
    storage.init()
    _insert_snapshot(storage, "2026-05-29T14:56:00+08:00", futures_price=4100)
    _insert_snapshot(storage, "2026-05-29T20:49:46+08:00", futures_price=9999)

    ref = storage.get_daily_reference_snapshot("IF", datetime(2026, 6, 1, 9, 35, tzinfo=TZ))

    assert ref is not None
    assert ref.timestamp.hour == 14
    assert ref.futures_price == 4100


def test_init_migrates_existing_snapshot_table_before_valid_index(tmp_path: Path):
    db_path = tmp_path / "market.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table snapshots (
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
            )
            """
        )

    Storage(db_path).init()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("pragma table_info(snapshots)").fetchall()}
        indexes = {row[1] for row in conn.execute("pragma index_list(snapshots)").fetchall()}

    assert "valid_for_scoring" in columns
    assert "idx_snapshots_product_valid_ts" in indexes


def test_daily_reference_falls_back_to_last_valid_day_session(tmp_path: Path):
    storage = Storage(tmp_path / "market.db")
    storage.init()
    _insert_snapshot(storage, "2026-05-29T10:15:00+08:00", futures_price=4050)
    _insert_snapshot(storage, "2026-05-29T20:49:46+08:00", futures_price=9999)

    ref = storage.get_daily_reference_snapshot("IF", datetime(2026, 6, 1, 9, 35, tzinfo=TZ))

    assert ref is not None
    assert ref.timestamp.hour == 10
    assert ref.futures_price == 4050


def test_label_due_predictions_uses_next_trading_day_calendar(tmp_path: Path):
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

    labeled = storage.label_due_predictions(datetime(2026, 6, 2, 10, 0, tzinfo=TZ))

    assert labeled == 1
    with storage._connect() as conn:
        row = conn.execute(
            """
            select target_trading_day, calendar_source
            from prediction_labels
            """
        ).fetchone()
    assert row["target_trading_day"] == "2026-06-02"
    assert row["calendar_source"] == "akshare"


def _insert_snapshot(storage: Storage, ts: str, futures_price: float) -> None:
    with storage._connect() as conn:
        conn.execute(
            """
            insert into snapshots (
                ts, product, contract, futures_price, futures_change_pct,
                spot_price, spot_change_pct, basis, basis_bp, volume,
                open_interest, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, "IF", "IF2606", futures_price, 0.1, 4000, 0.1, 100, 250, 1000, 2000, "{}"),
        )
