from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from futures_signal.market_calendar import TradingCalendar
from futures_signal.models import MarketAnalysis, ProductSignal
from futures_signal.storage import Storage, _direction_hit, _next_trading_day, _prediction_horizons, _target_time


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


def test_connect_ignores_missing_file_during_chmod(tmp_path: Path, monkeypatch):
    storage = Storage(tmp_path / "market.db")
    real_chmod = Path.chmod

    def fake_chmod(path_obj, mode):
        if path_obj == storage.db_path:
            raise FileNotFoundError("gone")
        return real_chmod(path_obj, mode)

    monkeypatch.setattr(Path, "chmod", fake_chmod)

    with storage._connect() as conn:
        assert conn.execute("select 1").fetchone()[0] == 1


def test_reference_snapshot_and_latest_score_return_none_when_empty(tmp_path: Path):
    storage = Storage(tmp_path / "market.db")
    storage.init()
    now = datetime(2026, 6, 1, 10, 0, tzinfo=TZ)

    assert storage.get_reference_snapshot("IF", now) is None
    assert storage.latest_score() == (None, None)


def test_save_analysis_persists_contract_change_and_predictions(tmp_path: Path):
    storage = Storage(tmp_path / "market.db")
    storage.init()
    analysis = _analysis(datetime(2026, 6, 1, 9, 45, tzinfo=TZ), main_contract_changed=True)

    storage.save_analysis(analysis)

    with storage._connect() as conn:
        score_row = conn.execute("select score, band from scores").fetchone()
        change_row = conn.execute(
            "select old_contract, new_contract from main_contract_changes where product = 'IF'"
        ).fetchone()
        horizons = [row["horizon"] for row in conn.execute("select horizon from predictions order by horizon").fetchall()]

    assert score_row["score"] == analysis.score
    assert change_row["old_contract"] == "IF2506"
    assert change_row["new_contract"] == "IF2606"
    assert horizons == ["same_day_1030", "same_day_1130", "same_day_close"]


def test_prediction_helpers_cover_tail_paths(tmp_path: Path):
    calendar = TradingCalendar(
        TZ,
        use_akshare=True,
        fetcher=lambda: ["2026-06-01", "2026-06-02"],
    )
    now = datetime(2026, 6, 1, 15, 6, tzinfo=TZ)

    assert _prediction_horizons(now) == []
    assert _target_time(now, "unknown", calendar) is None
    assert _next_trading_day(datetime(2026, 6, 5, 15, 6, tzinfo=TZ)).date().isoformat() == "2026-06-08"
    assert _target_time(now, "next_day_open", calendar).date().isoformat() == "2026-06-02"
    assert _direction_hit(50, 15) is True
    assert _direction_hit(50, 30) is False


def test_future_return_bp_and_nearest_price_cover_empty_paths(tmp_path: Path):
    storage = Storage(tmp_path / "market.db")
    storage.init()
    target = datetime(2026, 6, 2, 10, 30, tzinfo=TZ)

    with storage._connect() as conn:
        assert storage._future_return_bp(conn, {"signals": "bad"}, target) is None
        assert storage._future_return_bp(conn, {"signals": {"IF": []}}, target) is None
        assert storage._future_return_bp(conn, {"signals": {"IF": {"spot_price": None}}}, target) is None
        assert storage._future_return_bp(conn, {"signals": {"IF": {"spot_price": 4000}}}, target) is None
        assert storage._nearest_spot_price(conn, "IF", target, 30) is None


def test_label_due_predictions_delegates_to_evaluation_job(tmp_path: Path):
    storage = Storage(tmp_path / "market.db")
    storage.init()

    assert storage.label_due_predictions(datetime(2026, 6, 2, 10, 30, tzinfo=TZ), limit=10) == 0


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


def _analysis(timestamp: datetime, main_contract_changed: bool = False) -> MarketAnalysis:
    signal = ProductSignal(
        product="IF",
        product_name="沪深300",
        contract="IF2606",
        previous_contract="IF2506",
        futures_price=4010,
        futures_change_pct=0.2,
        spot_price=4000,
        spot_change_pct=0.1,
        basis=10,
        basis_bp=25,
        basis_state="升水",
        basis_change_bp=5,
        basis_change_label="走阔",
        basis_percentile=0.6,
        basis_zscore=0.2,
        basis_history_count=5,
        futures_minus_spot_pct=0.25,
        lead_beta=1.0,
        futures_return_5m_pct=0.1,
        spot_return_5m_pct=0.05,
        lead_residual_5m_pct=0.05,
        volume=1000,
        volume_change=100,
        volume_change_ratio=0.1,
        open_interest=2000,
        open_interest_change=50,
        price_change_5m=2.0,
        price_oi_signal="normal",
        main_contract_changed=main_contract_changed,
    )
    return MarketAnalysis(
        timestamp=timestamp,
        score=72,
        band="偏多",
        previous_score=60,
        previous_band="中性",
        components={},
        signals={"IF": signal},
        reasons=[],
        warnings=[],
        alert_kind="watch",
    )
