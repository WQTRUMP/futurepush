import builtins
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from futures_signal.akshare_source import AkShareDataSource, build_akshare_data_source
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


def test_wrapper_delegates_attributes_methods_and_property_access(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    source.settings = source.settings
    source._impl = source._impl

    source._last_position_date = "20260602"
    source._last_positions = {"IF": "rank"}
    source._last_position_empty_at = datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    source._last_position_trend_date = "20260602"
    source._last_position_trends = {"IF": "trend"}

    assert source._last_position_date == "20260602"
    assert source._last_positions == {"IF": "rank"}
    assert source._last_position_trend_date == "20260602"
    assert source._last_position_trends == {"IF": "trend"}

    marker = object()
    source.ak = marker
    source.quote_bundle_provider = marker
    source.calendar = marker
    source._last_term_fetch_at = marker
    source._last_terms = {"IF": []}
    assert source.ak is marker
    assert source.quote_bundle_provider is marker
    assert source.calendar is marker
    assert source._last_term_fetch_at is marker
    assert source._last_terms == {"IF": []}

    source.extra_value = "local"
    assert source.extra_value == "local"

    monkeypatch.setattr(source._impl, "fetch", lambda: "snapshot")
    assert source.fetch() == "snapshot"
    assert source.last_fetch_observation == source._impl.last_fetch_observation


def test_wrapper_proxy_helpers_and_builder_cover_delegated_paths(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    now = datetime(2026, 5, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    warnings: list[str] = []

    monkeypatch.setattr(source._impl.position_rank_provider, "_fetch_positions_for_date", lambda date_text, current, current_warnings: {"date": date_text})
    monkeypatch.setattr(
        source._impl.position_rank_provider,
        "_fetch_previous_available_positions",
        lambda current, current_warnings, max_lookback_days=7: {"lookback": max_lookback_days},
    )
    monkeypatch.setattr(source._impl.position_rank_provider, "_fetch_citic_net_short_changes", lambda date_text, current_warnings: {"IF": 1})
    monkeypatch.setattr(source._impl.position_trend_provider, "fetch", lambda current, current_warnings: ({"IF": "trend"}, source._impl.last_fetch_observation))

    assert source.__getattr__("_fetch_positions_if_due") == source._impl.position_rank_provider.fetch
    assert source.__getattr__("_fetch_positions_for_date") == source._impl.position_rank_provider._fetch_positions_for_date
    assert source.__getattr__("_fetch_citic_net_short_changes") == source._impl.position_rank_provider._fetch_citic_net_short_changes
    assert source.__getattr__("_fetch_position_trends_if_due") == source._impl.position_trend_provider.fetch

    assert source._fetch_positions_for_date("20260529", now, warnings) == {"date": "20260529"}
    assert source._fetch_previous_available_positions(now, warnings, max_lookback_days=3) == {"lookback": 3}
    assert source._fetch_citic_net_short_changes("20260529", warnings) == {"IF": 1}
    assert source._fetch_position_trends_if_due(now, warnings) == {"IF": "trend"}

    assert source._call_quiet(lambda: "quiet") == "quiet"
    assert isinstance(build_result := build_akshare_data_source(_settings(tmp_path)), AkShareDataSource)
    assert build_result._infer_product({"name": "上证50"}) == "IH"
