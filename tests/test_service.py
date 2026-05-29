from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from futures_signal.config import Settings
from futures_signal.models import FutureQuote, MarketSnapshot, SpotQuote
from futures_signal.service import _is_last_daily_window, run_once
from futures_signal.storage import Storage
from futures_signal.wecom import WeComClient


class FakeSource:
    def __init__(self, now):
        self.now = now
        self.count = 0

    def fetch(self):
        self.count += 1
        futures = {}
        spots = {}
        for product, price in {"IF": 4000, "IH": 2800, "IC": 6000, "IM": 6200}.items():
            futures[product] = FutureQuote(
                product=product,
                contract=f"{product}2606",
                name=product,
                price=price + self.count * 10,
                change_pct=0.5,
                volume=10000 + self.count * 100,
                open_interest=20000 + self.count * 100,
                tick_time=self.now,
            )
            spots[product] = SpotQuote(
                product=product,
                index_code="000000",
                name=product,
                price=price,
                change_pct=0.1,
                volume=None,
                amount=None,
                tick_time=self.now,
            )
        return MarketSnapshot(timestamp=self.now, futures=futures, spots=spots)


def test_run_once_persists_without_real_wecom(tmp_path: Path):
    settings = Settings(
        wecom_webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
        timezone_name="Asia/Shanghai",
        sample_interval_seconds=60,
        alert_cooldown_seconds=300,
        push_every_sample=True,
        run_outside_market_hours=True,
        use_trade_calendar=False,
        trade_calendar_cache_path=tmp_path / "trade_dates.json",
        fetch_term_structure=False,
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
    storage = Storage(settings.db_path)
    storage.init()
    source = FakeSource(datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    messenger = WeComClient("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test", dry_run=True)

    analysis, pushed = run_once(settings, storage, source, messenger, push=True)

    assert pushed is True
    assert analysis.score >= 40
    assert storage.latest_score()[0] == analysis.score


def test_daily_policy_pushes_once_in_scheduled_window(tmp_path: Path):
    settings = _settings(tmp_path, push_every_sample=False)
    storage = Storage(settings.db_path)
    storage.init()
    source = FakeSource(datetime(2026, 5, 27, 9, 36, tzinfo=ZoneInfo("Asia/Shanghai")))
    messenger = WeComClient("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test", dry_run=True)

    _, first_pushed = run_once(settings, storage, source, messenger, push=True)
    _, second_pushed = run_once(settings, storage, source, messenger, push=True)

    assert first_pushed is True
    assert second_pushed is False


def test_daily_policy_suppresses_regular_event_outside_windows(tmp_path: Path):
    settings = _settings(tmp_path, push_every_sample=False)
    storage = Storage(settings.db_path)
    storage.init()
    source = FakeSource(datetime(2026, 5, 27, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    messenger = WeComClient("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test", dry_run=True)

    _, pushed = run_once(settings, storage, source, messenger, push=True)

    assert pushed is False


def test_position_trend_only_on_last_daily_window(tmp_path: Path):
    settings = _settings(tmp_path, push_every_sample=False)

    assert _is_last_daily_window(
        settings,
        datetime(2026, 5, 27, 14, 31, tzinfo=ZoneInfo("Asia/Shanghai")),
        "daily_20260527_1430",
    )
    assert not _is_last_daily_window(
        settings,
        datetime(2026, 5, 27, 10, 31, tzinfo=ZoneInfo("Asia/Shanghai")),
        "daily_20260527_1030",
    )


def _settings(tmp_path: Path, push_every_sample: bool) -> Settings:
    return Settings(
        wecom_webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
        timezone_name="Asia/Shanghai",
        sample_interval_seconds=60,
        alert_cooldown_seconds=300,
        push_every_sample=push_every_sample,
        run_outside_market_hours=True,
        use_trade_calendar=False,
        trade_calendar_cache_path=tmp_path / "trade_dates.json",
        fetch_term_structure=False,
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
        deepseek_max_tokens=420,
        deepseek_temperature=0.2,
        deepseek_thinking_enabled=False,
        deepseek_reasoning_effort="high",
        log_level="INFO",
        data_dir=tmp_path,
        db_path=tmp_path / "market.db",
    )
