import builtins
from dataclasses import replace
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import futures_signal.akshare_source as akshare_module
from futures_signal.akshare_source import AkShareDataSource
from futures_signal.config import Settings
from futures_signal.data_sources import DataSourceError
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
                {"代码": "sh000300", "名称": "沪深300", "最新价": 4800, "涨跌幅": 0.1, "时间": "10:00:00"},
                {"代码": "sh000016", "名称": "上证50", "最新价": 2900, "涨跌幅": 0.2, "时间": "10:00:00"},
                {"代码": "sh000905", "名称": "中证500", "最新价": 8400, "涨跌幅": 0.3, "时间": "10:00:00"},
                {"代码": "sh000852", "名称": "中证1000", "最新价": 8500, "涨跌幅": 0.4, "时间": "10:00:00"},
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


def test_fetch_main_futures_uses_realtime_first(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    def fake_main_contract(symbol):
        return "IF2606,IH2606,IC2606,IM2606"

    def fake_spot(symbol, market, adjust):
        raise AssertionError("新浪批量源不应在逐品种实时源完整时调用")

    def fake_realtime(symbol):
        prefix = {
            "沪深300指数期货": "IF",
            "上证50指数期货": "IH",
            "中证500指数期货": "IC",
            "中证1000股指期货": "IM",
        }[symbol]
        return pd.DataFrame(
            [
                {
                    "symbol": f"{prefix}0",
                    "name": f"{prefix}连续",
                    "trade": 100,
                    "changepercent": 0.1,
                    "volume": 1000,
                    "position": 2000,
                    "ticktime": "10:00:00",
                },
                {
                    "symbol": f"{prefix}2606",
                    "name": f"{prefix}2606",
                    "trade": 101,
                    "changepercent": 0.2,
                    "volume": 1100,
                    "position": 2100,
                    "ticktime": "10:00:00",
                },
            ]
        )

    monkeypatch.setattr(source.ak, "match_main_contract", fake_main_contract)
    monkeypatch.setattr(source.ak, "futures_zh_spot", fake_spot)
    monkeypatch.setattr(source.ak, "futures_zh_realtime", fake_realtime)

    warnings: list[str] = []
    futures = source._fetch_main_futures(datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert set(futures) == {"IF", "IH", "IC", "IM"}
    assert futures["IF"].contract == "IF2606"
    assert futures["IF"].price == 101
    assert warnings == []


def test_init_raises_data_source_error_when_akshare_missing(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "akshare":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(DataSourceError, match="未安装 akshare"):
        AkShareDataSource(_settings(tmp_path))


def test_fetch_builds_snapshot_and_warns_for_missing_products(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    class FakeDateTime:
        @staticmethod
        def now(tz):
            return now

    monkeypatch.setattr(akshare_module, "datetime", FakeDateTime)
    monkeypatch.setattr(
        source,
        "_fetch_main_futures",
        lambda current, warnings: {
            "IF": type("Future", (), {"product": "IF"})(),
            "IH": type("Future", (), {"product": "IH"})(),
        },
    )
    monkeypatch.setattr(
        source,
        "_fetch_spots",
        lambda current, warnings: {
            "IF": type("Spot", (), {"product": "IF"})(),
            "IH": type("Spot", (), {"product": "IH"})(),
            "IC": type("Spot", (), {"product": "IC"})(),
        },
    )
    monkeypatch.setattr(source, "_fetch_terms_if_due", lambda current, spots, warnings: {"IF": []})
    monkeypatch.setattr(source, "_fetch_positions_if_due", lambda current, warnings: {"IF": object()})
    monkeypatch.setattr(source, "_fetch_position_trends_if_due", lambda current, warnings: {"IF": object()})

    snapshot = source.fetch()

    assert snapshot.timestamp == now
    assert snapshot.fetched_at == now
    assert snapshot.source == "akshare"
    assert "部分品种缺失: IC,IM" in snapshot.warnings


def test_fetch_raises_when_main_or_spot_data_missing(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    monkeypatch.setattr(source, "_fetch_main_futures", lambda current, warnings: {})
    monkeypatch.setattr(source, "_fetch_spots", lambda current, warnings: {"IF": object()})
    monkeypatch.setattr(source, "_fetch_terms_if_due", lambda current, spots, warnings: {})
    monkeypatch.setattr(source, "_fetch_positions_if_due", lambda current, warnings: {})
    monkeypatch.setattr(source, "_fetch_position_trends_if_due", lambda current, warnings: {})

    with pytest.raises(DataSourceError, match="期货主力行情"):
        source.fetch()

    monkeypatch.setattr(source, "_fetch_main_futures", lambda current, warnings: {"IF": object()})
    monkeypatch.setattr(source, "_fetch_spots", lambda current, warnings: {})

    with pytest.raises(DataSourceError, match="现货指数行情"):
        source.fetch()


def test_fetch_main_futures_falls_back_when_contract_list_missing(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    monkeypatch.setattr(source, "_main_contract_symbols", lambda warnings: [])
    monkeypatch.setattr(source, "_fetch_main_futures_from_realtime", lambda now, contracts, warnings: {})
    monkeypatch.setattr(source.ak, "futures_zh_spot", lambda **kwargs: pd.DataFrame([]))

    warnings: list[str] = []
    futures = source._fetch_main_futures(datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert futures == {}
    assert "主力合约列表不可用，已回退到新浪连续合约 IF0/IH0/IC0/IM0" in warnings
    assert "主力期货实时源和新浪批量补充源均返回空数据" in warnings


def test_fetch_main_futures_from_realtime_covers_warning_paths(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    def fake_realtime_rows(symbol):
        if symbol == PRODUCT_CONFIGS["IF"].future_name:
            raise RuntimeError("boom")
        if symbol == PRODUCT_CONFIGS["IH"].future_name:
            return []
        if symbol == PRODUCT_CONFIGS["IC"].future_name:
            return [{"symbol": "IC2606", "trade": 0}]
        return [{"symbol": "IM2606", "trade": 101, "ticktime": "10:00:00"}]

    monkeypatch.setattr(source, "_realtime_rows", fake_realtime_rows)

    warnings: list[str] = []
    futures = source._fetch_main_futures_from_realtime(
        datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ["IF2606", "IH2606", "IC2606", "IM2606"],
        warnings,
    )

    assert list(futures) == ["IM"]
    assert any("IF 逐品种期货实时源获取失败" in item for item in warnings)
    assert "IH 逐品种期货实时源未找到主力合约" in warnings
    assert "IC 逐品种期货实时源价格无效" in warnings


def test_select_main_future_row_prefers_matching_then_regex_then_first(tmp_path):
    source = AkShareDataSource(_settings(tmp_path))
    rows = [
        {"symbol": "IF0", "name": "IF连续"},
        {"symbol": "IF2606", "name": "IF2606"},
        {"symbol": "IF主力", "name": "IF主力"},
    ]

    assert source._select_main_future_row("IF", rows, "IF2606") == rows[1]
    assert source._select_main_future_row("IF", rows, None) == rows[1]
    assert source._select_main_future_row("IF", [{"symbol": "IF主力"}], None) == {"symbol": "IF主力"}


def test_main_contract_symbols_filters_and_handles_errors(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    monkeypatch.setattr(source.ak, "match_main_contract", lambda symbol: ["if2606", "bad", "IM2606"])

    warnings: list[str] = []
    assert source._main_contract_symbols(warnings) == ["IF2606", "IM2606"]

    monkeypatch.setattr(source.ak, "match_main_contract", lambda symbol: (_ for _ in ()).throw(RuntimeError("boom")))
    assert source._main_contract_symbols(warnings) == []
    assert any("match_main_contract 调用失败" in item for item in warnings)


def test_fetch_positions_aggregates_rank_sum_and_citic_change(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    def fake_rank_sum(date, vars_list):
        return pd.DataFrame(
            [
                {
                    "symbol": "IM2606",
                    "variety": "IM",
                    "long_open_interest_top20": 10000,
                    "long_open_interest_chg_top20": 200,
                    "short_open_interest_top20": 13000,
                    "short_open_interest_chg_top20": 1700,
                },
                {
                    "symbol": "IF2606",
                    "variety": "IF",
                    "long_open_interest_top20": 12000,
                    "long_open_interest_chg_top20": 900,
                    "short_open_interest_top20": 11000,
                    "short_open_interest_chg_top20": 100,
                },
            ]
        )

    def fake_cffex_rank_table(date, vars_list):
        return {
            "IM2606": pd.DataFrame(
                [
                    {
                        "long_party_name": "中信期货(代客)",
                        "long_open_interest_chg": 100,
                        "short_party_name": "中信期货(代客)",
                        "short_open_interest_chg": 849,
                    }
                ]
            )
        }

    monkeypatch.setattr(source.ak, "get_rank_sum", fake_rank_sum)
    monkeypatch.setattr(source.ak, "get_cffex_rank_table", fake_cffex_rank_table)

    warnings: list[str] = []
    positions = source._fetch_positions_if_due(datetime(2026, 5, 29, 16, 45, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert positions["IM"].net_short_top20 == 3000
    assert positions["IM"].net_short_change_top20 == 1500
    assert positions["IM"].citic_net_short_change == 749
    assert positions["IF"].net_short_change_top20 == -800
    assert warnings == []


def test_fetch_positions_uses_previous_available_day_when_today_empty(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    calls = []

    def fake_rank_sum(date, vars_list):
        calls.append(date)
        if date == "20260528":
            return pd.DataFrame(
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
            )
        return pd.DataFrame([])

    def fake_cffex_rank_table(date, vars_list):
        return {}

    monkeypatch.setattr(source.ak, "get_rank_sum", fake_rank_sum)
    monkeypatch.setattr(source.ak, "get_cffex_rank_table", fake_cffex_rank_table)

    warnings: list[str] = []
    positions = source._fetch_positions_if_due(datetime(2026, 5, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert positions["IM"].net_short_change_top20 == 1500
    assert positions["IM"].as_of_date == "20260528"
    assert positions["IM"].lag_days == 1
    assert positions["IM"].is_fallback is True
    assert "20260529" in calls
    assert "20260528" in calls
    assert any("已使用 20260528 排名" in item for item in warnings)

    calls.clear()
    second = source._fetch_positions_if_due(datetime(2026, 5, 29, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai")), [])

    assert second["IM"].net_short_change_top20 == 1500
    assert calls == []


def test_parse_tick_time_returns_none_for_invalid_value(tmp_path):
    source = AkShareDataSource(_settings(tmp_path))
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert source._parse_tick_time(now, {"ticktime": "bad"}) is None


def test_parse_spot_tick_time_returns_none_for_invalid_value(tmp_path):
    source = AkShareDataSource(_settings(tmp_path))
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert source._parse_spot_tick_time(now, {"时间": "bad"}) is None


def test_fetch_positions_fallback_uses_adjusted_trading_day(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    source.calendar = source.calendar.__class__(
        ZoneInfo("Asia/Shanghai"),
        use_akshare=True,
        fetcher=lambda: ["2026-06-05", "2026-06-06", "2026-06-08"],
    )
    calls = []

    def fake_rank_sum(date, vars_list):
        calls.append(date)
        if date == "20260606":
            return pd.DataFrame(
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
            )
        return pd.DataFrame([])

    monkeypatch.setattr(source.ak, "get_rank_sum", fake_rank_sum)
    monkeypatch.setattr(source.ak, "get_cffex_rank_table", lambda date, vars_list: {})

    warnings: list[str] = []
    positions = source._fetch_positions_if_due(datetime(2026, 6, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert positions["IM"].as_of_date == "20260606"
    assert "20260607" not in calls
    assert "20260606" in calls
    assert any("已使用 20260606 排名" in item for item in warnings)


def test_fetch_spots_uses_em_fallback_and_reports_missing(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    monkeypatch.setattr(
        source.ak,
        "stock_zh_index_spot_sina",
        lambda: pd.DataFrame([{"代码": "sh000300", "名称": "沪深300", "最新价": 4800, "涨跌幅": 0.1, "时间": "10:00:00"}]),
    )

    def fake_em(symbol):
        if symbol == "沪深重要指数":
            raise RuntimeError("timeout")
        if symbol == "上证系列指数":
            return pd.DataFrame([{"代码": "sh000016", "名称": "上证50", "最新价": 2900, "涨跌幅": 0.2, "时间": "10:00:00"}])
        return pd.DataFrame([])

    monkeypatch.setattr(source.ak, "stock_zh_index_spot_em", fake_em)

    warnings: list[str] = []
    spots = source._fetch_spots(datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert set(spots) == {"IF", "IH"}
    assert any("现货指数 沪深重要指数 补充源获取失败" in item for item in warnings)
    assert "现货指数已使用东方财富补充源" in warnings
    assert "现货指数缺失: IC,IM" in warnings


def test_fetch_spots_from_sina_warns_on_error_and_fallback(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    wanted = {"000300": "IF"}
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    monkeypatch.setattr(source.ak, "stock_zh_index_spot_sina", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    warnings: list[str] = []
    assert source._fetch_spots_from_sina(now, wanted, warnings) == {}
    assert any("新浪指数备用源获取失败" in item for item in warnings)

    monkeypatch.setattr(
        source.ak,
        "stock_zh_index_spot_sina",
        lambda: pd.DataFrame([{"代码": "sh000300", "名称": "沪深300", "最新价": 4800, "涨跌幅": 0.1, "时间": "10:00:00"}]),
    )
    warnings.clear()
    assert "IF" in source._fetch_spots_from_sina(now, wanted, warnings)
    assert warnings == ["现货指数已使用新浪备用源"]


def test_fetch_position_trends_aggregates_recent_days(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    def fake_rank_sum_daily(start_day, end_day, vars_list):
        return pd.DataFrame(
            [
                {
                    "date": "20260525",
                    "symbol": "IM2606",
                    "variety": "IM",
                    "long_open_interest_chg_top20": 100,
                    "short_open_interest_chg_top20": 600,
                },
                {
                    "date": "20260526",
                    "symbol": "IM2606",
                    "variety": "IM",
                    "long_open_interest_chg_top20": 200,
                    "short_open_interest_chg_top20": 900,
                },
                {
                    "date": "20260527",
                    "symbol": "IF2606",
                    "variety": "IF",
                    "long_open_interest_chg_top20": 900,
                    "short_open_interest_chg_top20": 100,
                },
            ]
        )

    monkeypatch.setattr(source.ak, "get_rank_sum_daily", fake_rank_sum_daily)

    warnings: list[str] = []
    trends = source._fetch_position_trends_if_due(datetime(2026, 5, 29, 16, 45, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert trends["IM"].net_short_change_sum == 1200
    assert trends["IM"].latest_net_short_change == 700
    assert trends["IF"].net_short_change_sum == -800
    assert warnings == []


def test_fetch_terms_and_positions_cover_disabled_and_retry_paths(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    source = AkShareDataSource(settings)
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    disabled_source = AkShareDataSource(replace(settings, fetch_term_structure=False))
    assert disabled_source._fetch_terms_if_due(now, {}, []) == {}

    monkeypatch.setattr(source, "_realtime_rows", lambda symbol: (_ for _ in ()).throw(RuntimeError("boom")))
    warnings: list[str] = []
    assert source._fetch_terms_if_due(now, {"IF": type("Spot", (), {"price": 4000})()}, warnings) == {}
    assert any("IF 期限结构获取失败" in item for item in warnings)

    disabled_positions = AkShareDataSource(replace(settings, fetch_position_rank=False))
    assert disabled_positions._fetch_positions_if_due(now, []) == {}

    source._last_position_date = now.strftime("%Y%m%d")
    source._last_positions = {"IF": object()}
    assert source._fetch_positions_if_due(now, []) == source._last_positions

    source._last_position_date = None
    source._last_position_empty_at = now
    source._last_positions = {"IF": object()}
    warnings.clear()
    assert source._fetch_positions_if_due(now + timedelta(seconds=30), warnings) == source._last_positions
    assert warnings == ["今日持仓排名暂不可用，继续使用上一可用交易日排名"]

    source._last_positions = {}
    warnings.clear()
    assert source._fetch_positions_if_due(now + timedelta(seconds=30), warnings) == {}
    assert warnings == ["今日持仓排名暂不可用，等待下次重试"]


def test_position_fetch_helpers_cover_exceptions_and_empty_fallback(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    monkeypatch.setattr(source.ak, "get_rank_sum", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    warnings: list[str] = []
    assert source._fetch_positions_for_date("20260528", now, warnings) == {}
    assert any("中金所持仓汇总获取失败" in item for item in warnings)

    monkeypatch.setattr(source, "_fetch_positions_for_date", lambda date_text, current, messages: {})
    source.calendar = source.calendar.__class__(ZoneInfo("Asia/Shanghai"), use_akshare=True, fetcher=lambda: ["2026-05-28"])
    warnings.clear()
    assert source._fetch_previous_available_positions(now, warnings, max_lookback_days=2) == {}
    assert warnings == ["今日及上一可用交易日持仓排名均不可用，本次持仓排名降为中性"]


def test_fetch_citic_and_position_trends_cover_edge_paths(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    monkeypatch.setattr(source.ak, "get_cffex_rank_table", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    warnings: list[str] = []
    assert source._fetch_citic_net_short_changes("20260528", warnings) == {}
    assert any("中信期货席位持仓获取失败" in item for item in warnings)

    monkeypatch.setattr(source.ak, "get_cffex_rank_table", lambda **kwargs: [])
    assert source._fetch_citic_net_short_changes("20260528", warnings) == {}

    disabled_trends = AkShareDataSource(replace(_settings(tmp_path), position_trend_days=1))
    assert disabled_trends._fetch_position_trends_if_due(now, []) == {}

    source._last_position_trend_date = now.strftime("%Y%m%d")
    source._last_position_trends = {"IF": object()}
    assert source._fetch_position_trends_if_due(now, []) == source._last_position_trends

    source._last_position_trend_date = None
    monkeypatch.setattr(source.ak, "get_rank_sum_daily", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    warnings.clear()
    assert source._fetch_position_trends_if_due(now, warnings) == {}
    assert any("近期期货持仓趋势获取失败" in item for item in warnings)


def test_fetch_clears_realtime_cache_even_when_fetch_fails(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    def boom(now, warnings):
        source._realtime_cache["temp"] = [{"symbol": "IF2606"}]
        raise RuntimeError("boom")

    monkeypatch.setattr(source, "_fetch_main_futures", boom)

    with pytest.raises(RuntimeError, match="boom"):
        source.fetch()

    assert source._realtime_cache == {}


def test_fetch_terms_reuses_recent_cache_and_filters_invalid_contracts(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    calls = {"IF": 0}

    def fake_realtime_rows(symbol):
        if symbol == PRODUCT_CONFIGS["IF"].future_name:
            calls["IF"] += 1
            return [
                {"symbol": "IF0", "trade": 100, "position": 1, "volume": 2, "ticktime": "10:00:00"},
                {"symbol": "IF2607", "trade": 4020, "position": 2000, "volume": 1000, "ticktime": "10:00:00"},
                {"symbol": "IF2608", "trade": 0, "position": 3000, "volume": 1200, "ticktime": "10:01:00"},
            ]
        return []

    monkeypatch.setattr(source, "_realtime_rows", fake_realtime_rows)

    spots = {"IF": type("Spot", (), {"price": 4000})()}
    terms = source._fetch_terms_if_due(now, spots, [])
    cached = source._fetch_terms_if_due(now + timedelta(seconds=60), spots, [])

    assert calls["IF"] == 1
    assert cached is terms
    assert [term.contract for term in terms["IF"]] == ["IF2607"]
    assert terms["IF"][0].basis == 20


def test_parse_datetime_value_handles_aware_isoformat(tmp_path):
    source = AkShareDataSource(_settings(tmp_path))

    parsed = source._parse_datetime_value("2026-05-28T10:00:00+00:00", "2026-05-28")

    assert parsed is not None
    assert parsed.tzinfo == ZoneInfo("Asia/Shanghai")
    assert parsed.hour == 18


def test_parse_datetime_value_assigns_timezone_to_naive_isoformat(tmp_path):
    source = AkShareDataSource(_settings(tmp_path))

    parsed = source._parse_datetime_value("2026-05-28T10:00:00", "2026-05-28")

    assert parsed is not None
    assert parsed.tzinfo == ZoneInfo("Asia/Shanghai")
    assert parsed.hour == 10


def test_datetime_parsing_and_realtime_cache_handle_monkeypatched_datetime(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))

    class FakeDateTime:
        @staticmethod
        def now(tz):
            return datetime(2026, 5, 28, 10, 0, tzinfo=tz)

    monkeypatch.setattr(akshare_module, "datetime", FakeDateTime)

    parsed = source._parse_datetime_value("2026-05-28 10:00:00", "2026-05-28")
    assert parsed is not None
    assert parsed.hour == 10

    calls = {"count": 0}

    def fake_realtime(symbol):
        calls["count"] += 1
        return pd.DataFrame([{"symbol": "IF2606", "trade": 100, "ticktime": "10:00:00"}])

    monkeypatch.setattr(source.ak, "futures_zh_realtime", fake_realtime)
    assert source._realtime_rows("沪深300指数期货")[0]["symbol"] == "IF2606"
    assert source._realtime_rows("沪深300指数期货")[0]["symbol"] == "IF2606"
    assert calls["count"] == 1


def test_helper_methods_cover_remaining_branches(tmp_path):
    source = AkShareDataSource(_settings(tmp_path))

    assert source._rows(None) == []
    assert source._rows([{"a": 1}, "bad"]) == [{"a": 1}]
    assert source._rows("bad") == []
    assert source._brief_error(type("ProxyError", (Exception,), {})("proxy")) == "ProxyError: 代理或网络连接失败"
    assert source._brief_error(TimeoutError("timeout")) == "TimeoutError: 请求超时"
    assert source._brief_error(ConnectionError("conn")) == "ConnectionError: 网络连接失败"
    assert source._brief_error(RuntimeError("x" * 300)).endswith("...")
    assert source._product_from_contract("bad") is None
    assert source._infer_product({"name": "沪深300"}) == "IF"
    assert source._infer_product({"name": "上证50"}) == "IH"
    assert source._infer_product({"name": "中证500"}) == "IC"
    assert source._infer_product({"name": "中证1000"}) == "IM"
    assert source._infer_product({"name": "unknown"}) is None
    assert source._infer_contract({"symbol": "none"}) is None
    assert source._first_float({}, ["price"], None) is None
    assert source._first_int({}, ["volume"], None) is None
    assert source._change_pct({"preclose": 100}, 110) == pytest.approx(10)
    assert source._change_pct({}, 110) == 0.0


def test_realtime_rows_uses_symbol_cache(tmp_path, monkeypatch):
    source = AkShareDataSource(_settings(tmp_path))
    calls = {"count": 0}

    def fake_realtime(symbol):
        calls["count"] += 1
        return pd.DataFrame([{"symbol": "IF2606", "trade": 100}])

    monkeypatch.setattr(source.ak, "futures_zh_realtime", fake_realtime)

    first = source._realtime_rows("沪深300指数期货")
    second = source._realtime_rows("沪深300指数期货")

    assert first == second
    assert calls["count"] == 1
