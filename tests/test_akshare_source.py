import builtins
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from futures_signal.akshare_source import AkShareDataSource
from futures_signal.config import Settings
from futures_signal.data_sources import DataSourceError


def _settings(tmp_path: Path) -> Settings:
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
        fetch_position_rank=True,
        position_trend_days=5,
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
        log_level="DEBUG",
        data_dir=tmp_path,
        db_path=tmp_path / "market.db",
    )


def test_init_raises_data_source_error_when_akshare_missing(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "akshare":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(DataSourceError, match="未安装 akshare"):
        AkShareDataSource(_settings(tmp_path))


def test_wrapper_exposes_static_helpers_and_provider_state(tmp_path):
    source = AkShareDataSource(_settings(tmp_path))

    assert source._rows(None) == []
    assert source._rows([{"a": 1}, "bad"]) == [{"a": 1}]
    assert source._brief_error(type("ProxyError", (Exception,), {})("proxy")) == "ProxyError: 代理或网络连接失败"
    assert source._brief_error(TimeoutError("timeout")) == "TimeoutError: 请求超时"
    assert source._brief_error(ConnectionError("conn")) == "ConnectionError: 网络连接失败"
    assert source._product_from_contract("IF2606") == "IF"
    assert source._infer_product({"name": "沪深300"}) == "IF"
    assert source._infer_contract({"symbol": "IM2606"}) == "IM2606"
    assert source._first_float({}, ["price"], None) is None
    assert source._first_int({}, ["volume"], None) is None

    source._last_position_date = "20260601"
    source._last_position_trend_date = "20260601"

    assert source.position_rank_provider._last_position_date == "20260601"
    assert source.position_trend_provider._last_position_trend_date == "20260601"


def test_parse_datetime_value_and_bundle_cache_handle_cached_delegate(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    parsed = source._parse_datetime_value("2026-05-28T10:00:00+00:00", "2026-05-28")
    assert parsed is not None
    assert parsed.tzinfo == ZoneInfo("Asia/Shanghai")
    assert parsed.hour == 18

    parsed = source._parse_datetime_value("2026-05-28T10:00:00", "2026-05-28")
    assert parsed is not None
    assert parsed.tzinfo == ZoneInfo("Asia/Shanghai")
    assert parsed.hour == 10

    calls = {"count": 0}

    def fake_realtime(symbol):
        calls["count"] += 1
        return pd.DataFrame([{"symbol": "IF2606", "trade": 100, "ticktime": "10:00:00"}])

    monkeypatch.setattr(source.ak, "futures_zh_realtime", fake_realtime)
    bundle = source.quote_bundle_provider.create()
    assert source._realtime_rows("沪深300指数期货", bundle=bundle)[0]["symbol"] == "IF2606"
    assert source._realtime_rows("沪深300指数期货", bundle=bundle)[0]["symbol"] == "IF2606"
    assert calls["count"] == 1


def test_fetch_position_proxy_methods_delegate_to_split_providers(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    now = datetime(2026, 5, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    monkeypatch.setattr(
        source.position_rank_provider.ak,
        "get_rank_sum",
        lambda date, vars_list: pd.DataFrame(
            [
                {
                    "symbol": "IM2606",
                    "variety": "IM",
                    "long_open_interest_top20": 10000,
                    "long_open_interest_chg_top20": 200,
                    "short_open_interest_top20": 13000,
                    "short_open_interest_chg_top20": 1700,
                }
            ]
        ),
    )
    monkeypatch.setattr(source.position_rank_provider.ak, "get_cffex_rank_table", lambda date, vars_list: {})
    monkeypatch.setattr(
        source.position_trend_provider.ak,
        "get_rank_sum_daily",
        lambda start_day, end_day, vars_list: pd.DataFrame(
            [
                {
                    "date": "20260528",
                    "symbol": "IM2606",
                    "variety": "IM",
                    "long_open_interest_chg_top20": 100,
                    "short_open_interest_chg_top20": 300,
                }
            ]
        ),
    )

    warnings: list[str] = []
    positions = source._fetch_positions_if_due(now, warnings)
    trends = source._fetch_position_trends_if_due(now, warnings)

    assert positions["IM"].net_short_change_top20 == 1500
    assert trends["IM"].latest_net_short_change == 200
    assert warnings == []
