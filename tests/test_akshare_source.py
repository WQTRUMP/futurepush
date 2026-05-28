from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from futures_signal.akshare_source import AkShareDataSource
from futures_signal.config import Settings
from futures_signal.models import PRODUCT_CONFIGS


def _settings(tmp_path: Path):
    return Settings(
        wecom_webhook_url="",
        timezone_name="Asia/Shanghai",
        sample_interval_seconds=60,
        alert_cooldown_seconds=300,
        push_every_sample=False,
        run_outside_market_hours=True,
        use_trade_calendar=False,
        trade_calendar_cache_path=tmp_path / "trade_dates.json",
        fetch_term_structure=True,
        fetch_term_structure_every_seconds=300,
        dividend_season_adjust=True,
        basis_history_days=20,
        roll_window_days=7,
        ai_commentary_enabled=False,
        deepseek_api_key="",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-pro",
        deepseek_timeout_seconds=20,
        deepseek_max_tokens=260,
        deepseek_temperature=0.2,
        deepseek_thinking_enabled=False,
        deepseek_reasoning_effort="high",
        log_level="INFO",
        data_dir=tmp_path,
        db_path=tmp_path / "market.db",
    )


def test_product_future_names_match_akshare_symbol_mark_names():
    assert PRODUCT_CONFIGS["IF"].future_name == "沪深300指数期货"
    assert PRODUCT_CONFIGS["IH"].future_name == "上证50指数期货"
    assert PRODUCT_CONFIGS["IC"].future_name == "中证500指数期货"
    assert PRODUCT_CONFIGS["IM"].future_name == "中证1000股指期货"


def test_fetch_spots_uses_sina_first_and_skips_em_when_complete(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    def fake_sina():
        return pd.DataFrame(
            [
                {"代码": "sh000300", "名称": "沪深300", "最新价": 4800, "涨跌幅": 0.1},
                {"代码": "sh000016", "名称": "上证50", "最新价": 2900, "涨跌幅": 0.2},
                {"代码": "sh000905", "名称": "中证500", "最新价": 8400, "涨跌幅": 0.3},
                {"代码": "sh000852", "名称": "中证1000", "最新价": 8500, "涨跌幅": 0.4},
            ]
        )

    def fake_em(symbol):
        raise AssertionError("东方财富接口不应在新浪数据完整时调用")

    monkeypatch.setattr(source.ak, "stock_zh_index_spot_sina", fake_sina)
    monkeypatch.setattr(source.ak, "stock_zh_index_spot_em", fake_em)

    warnings: list[str] = []
    spots = source._fetch_spots(datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert set(spots) == {"IF", "IH", "IC", "IM"}
    assert warnings == []
