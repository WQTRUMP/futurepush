from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from futures_signal.config import Settings
from futures_signal.market_calendar import TradingCalendar
from futures_signal.providers.positions import PositionRankProvider


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


def _provider(tmp_path: Path, settings: Settings | None = None) -> PositionRankProvider:
    settings = settings or _settings(tmp_path)
    return PositionRankProvider(
        ak=type(
            "FakeAk",
            (),
            {
                "get_rank_sum": lambda self, *args, **kwargs: None,
                "get_cffex_rank_table": lambda self, *args, **kwargs: None,
            },
        )(),
        settings=settings,
        calendar=TradingCalendar(settings.tz, use_akshare=False, cache_path=settings.trade_calendar_cache_path),
    )


def test_fetch_aggregates_rank_sum_and_citic_change(tmp_path, monkeypatch):
    provider = _provider(tmp_path)

    monkeypatch.setattr(
        provider.ak,
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
        ),
    )
    monkeypatch.setattr(
        provider.ak,
        "get_cffex_rank_table",
        lambda date, vars_list: {
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
        },
    )

    warnings: list[str] = []
    positions, observation = provider.fetch(datetime(2026, 5, 29, 16, 45, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert positions["IM"].net_short_top20 == 3000
    assert positions["IM"].citic_net_short_change == 749
    assert positions["IF"].net_short_change_top20 == -800
    assert observation.status == "ok"
    assert warnings == []


def test_fetch_uses_previous_available_day_and_reports_observation(tmp_path, monkeypatch):
    provider = _provider(tmp_path)
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

    monkeypatch.setattr(provider.ak, "get_rank_sum", fake_rank_sum)
    monkeypatch.setattr(provider.ak, "get_cffex_rank_table", lambda date, vars_list: {})

    warnings: list[str] = []
    positions, observation = provider.fetch(datetime(2026, 5, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert positions["IM"].as_of_date == "20260528"
    assert positions["IM"].is_fallback is True
    assert observation.status == "degraded"
    assert observation.details["fallback"] is True
    assert "20260529" in calls
    assert "20260528" in calls
    assert any("已使用 20260528 排名" in item for item in warnings)


def test_fetch_honors_retry_cooldown_and_adjusted_trading_day(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = _provider(tmp_path, settings=settings)
    now = datetime(2026, 6, 8, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    provider.calendar = TradingCalendar(
        settings.tz,
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

    monkeypatch.setattr(provider.ak, "get_rank_sum", fake_rank_sum)
    monkeypatch.setattr(provider.ak, "get_cffex_rank_table", lambda date, vars_list: {})

    warnings: list[str] = []
    positions, observation = provider.fetch(now, warnings)
    assert positions["IM"].as_of_date == "20260606"
    assert "20260607" not in calls
    assert observation.details["fallback"] is True

    provider._last_position_empty_at = now
    provider._last_positions = {"IF": object()}
    warnings.clear()
    reused, observation = provider.fetch(now + timedelta(seconds=30), warnings)
    assert reused == provider._last_positions
    assert observation.status == "degraded"
    assert observation.details["cooldown_active"] is True
    assert warnings == ["今日持仓排名暂不可用，继续使用上一可用交易日排名"]


def test_fetch_covers_disabled_and_failure_paths(tmp_path, monkeypatch):
    disabled = _provider(tmp_path, settings=replace(_settings(tmp_path), fetch_position_rank=False))
    assert disabled.fetch(datetime(2026, 5, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), [])[1].status == "skipped"

    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.ak, "get_rank_sum", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    warnings: list[str] = []
    positions, observation = provider.fetch(datetime(2026, 5, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)
    assert positions == {}
    assert observation.status == "degraded"
    assert any("中金所持仓汇总获取失败" in item for item in warnings)
