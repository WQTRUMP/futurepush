from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from futures_signal.config import Settings
from futures_signal.providers.position_trends import PositionTrendProvider


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


def _provider(tmp_path: Path, settings: Settings | None = None) -> PositionTrendProvider:
    settings = settings or _settings(tmp_path)
    return PositionTrendProvider(
        ak=type("FakeAk", (), {"get_rank_sum_daily": lambda self, *args, **kwargs: None})(),
        settings=settings,
    )


def test_fetch_aggregates_recent_days_and_reuses_cache(tmp_path, monkeypatch):
    provider = _provider(tmp_path)

    monkeypatch.setattr(
        provider.ak,
        "get_rank_sum_daily",
        lambda start_day, end_day, vars_list: pd.DataFrame(
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
        ),
    )

    warnings: list[str] = []
    trends, observation = provider.fetch(datetime(2026, 5, 29, 16, 45, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)

    assert trends["IM"].net_short_change_sum == 1200
    assert trends["IF"].net_short_change_sum == -800
    assert observation.status == "ok"
    assert warnings == []

    cached, observation = provider.fetch(datetime(2026, 5, 29, 17, 0, tzinfo=ZoneInfo("Asia/Shanghai")), [])
    assert cached == trends
    assert observation.details["cache_hit"] is True


def test_fetch_covers_disabled_and_failure_paths(tmp_path, monkeypatch):
    disabled = _provider(tmp_path, settings=replace(_settings(tmp_path), position_trend_days=1))
    assert disabled.fetch(datetime(2026, 5, 29, 16, 45, tzinfo=ZoneInfo("Asia/Shanghai")), [])[1].status == "skipped"

    provider = _provider(tmp_path)
    monkeypatch.setattr(provider.ak, "get_rank_sum_daily", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    warnings: list[str] = []
    trends, observation = provider.fetch(datetime(2026, 5, 29, 16, 45, tzinfo=ZoneInfo("Asia/Shanghai")), warnings)
    assert trends == {}
    assert observation.status == "failed"
    assert any("近期期货持仓趋势获取失败" in item for item in warnings)
