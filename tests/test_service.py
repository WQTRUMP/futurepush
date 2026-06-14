from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from futures_signal import service
from futures_signal.ai_commentary import AICommentaryError
from futures_signal.config import Settings
from futures_signal.models import FutureQuote, MarketAnalysis, MarketSnapshot, SpotQuote
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


def test_setup_runtime_dirs_sets_restricted_permissions(tmp_path: Path):
    settings = _settings(tmp_path, push_every_sample=False)

    service.setup_runtime_dirs(settings)

    assert oct(settings.data_dir.stat().st_mode & 0o777) == "0o700"
    assert oct(settings.db_path.parent.stat().st_mode & 0o777) == "0o700"


def test_generate_ai_commentary_hides_internal_error_message(tmp_path: Path):
    class BrokenAIClient:
        def generate(self, analysis):
            raise AICommentaryError("secret token")

    analysis = MarketAnalysis(
        timestamp=datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        score=72,
        band="偏多",
        previous_score=60,
        previous_band="中性",
        components={},
        signals={},
        reasons=[],
        warnings=[],
        alert_kind="test",
    )
    result = service._generate_ai_commentary(
        BrokenAIClient(),
        analysis,
    )

    assert result == "AI点评暂不可用，请查看系统日志。"


def test_run_forever_uses_calendar_for_storage_and_sleep(tmp_path: Path, monkeypatch):
    settings = replace(_settings(tmp_path, push_every_sample=False), run_outside_market_hours=False)
    created = {}
    fake_now = datetime(2026, 5, 30, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    class FakeCalendar:
        source = "cache"
        warning = "stale cache"

        def is_market_open(self, now):
            return False

        def seconds_until_next_session(self, now):
            return 5000

        def is_trading_day(self, day):
            return False

    class FakeStorage:
        def __init__(self, db_path, calendar):
            created["db_path"] = db_path
            created["calendar"] = calendar
            created["inited"] = False

        def init(self):
            created["inited"] = True

    class FakeDateTime:
        @staticmethod
        def now(tz):
            return fake_now

    monkeypatch.setattr(service, "setup_runtime_dirs", lambda _settings: None)
    monkeypatch.setattr(service, "TradingCalendar", lambda *args, **kwargs: FakeCalendar())
    monkeypatch.setattr(service, "Storage", FakeStorage)
    monkeypatch.setattr(service, "AkShareDataSource", lambda _settings: object())
    monkeypatch.setattr(service, "WeComClient", lambda webhook_url: {"webhook_url": webhook_url})
    monkeypatch.setattr(service, "AICommentaryClient", lambda _settings: object())
    monkeypatch.setattr(service, "datetime", FakeDateTime)

    def stop_sleep(seconds):
        raise RuntimeError(seconds)

    monkeypatch.setattr(service.time, "sleep", stop_sleep)

    with pytest.raises(RuntimeError, match="3600"):
        service.run_forever(settings)

    assert created["db_path"] == settings.db_path
    assert isinstance(created["calendar"], FakeCalendar)
    assert created["inited"] is True


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
