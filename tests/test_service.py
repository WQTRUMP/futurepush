from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from futures_signal import service
from futures_signal.ai_commentary import AICommentaryError
from futures_signal.config import Settings
from futures_signal.models import FutureQuote, MarketAnalysis, MarketSnapshot, SpotQuote
from futures_signal.service import _is_last_daily_window, build_sampling_context, run_once
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


def test_build_sampling_context_batches_storage_reads(tmp_path: Path):
    settings = _settings(tmp_path, push_every_sample=False)
    now = datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    source = FakeSource(now)
    calls: list[tuple[str, str, datetime, int | None]] = []

    class SpyStorage(Storage):
        def get_reference_snapshot(self, product, timestamp, lookback_minutes=5, max_age_minutes=30):
            calls.append(("reference", product, timestamp, None))
            return super().get_reference_snapshot(product, timestamp, lookback_minutes, max_age_minutes)

        def get_daily_reference_snapshot(self, product, timestamp):
            calls.append(("daily_reference", product, timestamp, None))
            return super().get_daily_reference_snapshot(product, timestamp)

        def get_basis_history(self, product, timestamp, days=20):
            calls.append(("basis_history", product, timestamp, days))
            return super().get_basis_history(product, timestamp, days)

        def latest_contracts(self):
            calls.append(("latest_contracts", "*", now, None))
            return super().latest_contracts()

        def latest_score(self):
            calls.append(("latest_score", "*", now, None))
            return super().latest_score()

    storage = SpyStorage(settings.db_path)
    storage.init()

    context = build_sampling_context(
        settings,
        storage,
        source,
        service.TradingCalendar(
            settings.tz,
            use_akshare=settings.use_trade_calendar,
            cache_path=settings.trade_calendar_cache_path,
        ),
        save_outside_market=False,
    )

    assert context.snapshot.timestamp == now
    assert calls.count(("latest_contracts", "*", now, None)) == 1
    assert calls.count(("latest_score", "*", now, None)) == 1
    for product in ("IF", "IH", "IC", "IM"):
        assert ("reference", product, now, None) in calls
        assert ("daily_reference", product, now, None) in calls
        assert ("basis_history", product, now, settings.basis_history_days) in calls


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


def test_generate_ai_commentary_returns_none_without_client(tmp_path: Path):
    analysis = _analysis(datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert service._generate_ai_commentary(None, analysis) is None


def test_prepare_snapshot_marks_outside_market_as_non_persistent(tmp_path: Path):
    settings = replace(_settings(tmp_path, push_every_sample=False), run_outside_market_hours=False)
    snapshot = FakeSource(datetime(2026, 5, 27, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai"))).fetch()

    class ClosedCalendar:
        def is_market_open(self, now):
            return False

    prepared, should_persist = service._prepare_snapshot(settings, snapshot, ClosedCalendar(), False)

    assert should_persist is False
    assert prepared.valid_for_scoring is False
    assert prepared.fetched_at == snapshot.timestamp
    assert prepared.warnings[-1] == "非交易时段样本仅展示，未入库也不会推送"


def test_persist_analysis_side_effects_skips_when_not_persisting(tmp_path: Path):
    calls = []

    class FakeStorage:
        def save_analysis(self, analysis):
            calls.append("save_analysis")

        def label_due_predictions(self, timestamp):
            calls.append("label_due_predictions")

    service.persist_analysis_side_effects(
        FakeStorage(),
        _analysis(datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))),
        False,
    )

    assert calls == []


def test_dispatch_alert_if_needed_builds_default_messenger(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path, push_every_sample=True)
    analysis = _analysis(datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), alert_kind="strong_long")
    sent = {}

    class FakeStorage:
        def has_recent_alert(self, kind, timestamp, cooldown):
            return False

        def save_alert(self, timestamp, kind, band, score, message):
            sent["saved"] = (timestamp, kind, band, score, message)

    class FakeMessenger:
        def __init__(self, webhook_url):
            sent["webhook_url"] = webhook_url

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(service, "WeComClient", FakeMessenger)

    pushed = service.dispatch_alert_if_needed(
        settings,
        FakeStorage(),
        analysis,
        True,
        push=True,
        messenger=None,
        ai_client=None,
    )

    assert pushed is True
    assert sent["webhook_url"] == settings.wecom_webhook_url
    assert sent["saved"][1] == "sample"
    assert sent["message"]
    assert sent["saved"][4] == sent["message"]


def test_should_push_and_alert_helpers_cover_policy_variants(tmp_path: Path, monkeypatch):
    settings = replace(
        _settings(tmp_path, push_every_sample=False),
        push_policy="daily",
        daily_push_times="bad,14:30,,10:30",
    )
    analysis = _analysis(
        datetime(2026, 5, 27, 14, 31, tzinfo=ZoneInfo("Asia/Shanghai")),
        score=82,
        alert_kind="strong_long",
    )

    class FakeStorage:
        def __init__(self, seen):
            self.seen = seen

        def has_recent_alert(self, kind, timestamp, cooldown):
            self.seen.append((kind, cooldown))
            return False

    warnings = []
    monkeypatch.setattr(service.logger, "warning", lambda message, text: warnings.append((message, text)))

    assert service._alert_kind(settings, analysis) == "daily_20260527_1430"
    assert service._should_push(settings, FakeStorage(warnings), analysis) is True
    assert ("daily_20260527_1430", settings.daily_alert_cooldown_seconds) in warnings
    assert service._alert_cooldown_seconds(settings, "daily_urgent_bullish") == settings.urgent_alert_cooldown_seconds
    assert service._alert_cooldown_seconds(settings, "daily_20260527_1430") == settings.daily_alert_cooldown_seconds
    assert service._daily_urgent_kind(_analysis(analysis.timestamp, score=10, alert_kind="strong_short")) == "daily_urgent_bearish"
    assert service._daily_urgent_kind(_analysis(analysis.timestamp, score=50, alert_kind="watch")) is None
    assert service._last_daily_push_time(replace(settings, daily_push_times="bad,,")) is None
    assert warnings[0] == ("invalid DAILY_PUSH_TIMES item: %s", "bad")


def test_alert_kind_event_and_fallback_policy(tmp_path: Path):
    analysis = _analysis(datetime(2026, 5, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")), alert_kind="watch")

    event_settings = replace(_settings(tmp_path, push_every_sample=False), push_policy="event")
    fallback_settings = replace(_settings(tmp_path, push_every_sample=False), push_policy="unknown")

    assert service._alert_kind(event_settings, analysis) == "watch"
    assert service._alert_kind(fallback_settings, analysis) == "watch"


def test_run_forever_handles_open_market_success_and_error(tmp_path: Path, monkeypatch):
    settings = replace(_settings(tmp_path, push_every_sample=False), run_outside_market_hours=False)
    created = {"run_once_calls": 0}
    health = {"ok": 0, "error": 0}
    fake_now = datetime(2026, 5, 30, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    analysis = _analysis(fake_now)

    class FakeCalendar:
        source = "cache"
        warning = None

        def is_market_open(self, now):
            return True

    class FakeStorage:
        def __init__(self, db_path, calendar):
            created["calendar"] = calendar

        def init(self):
            created["inited"] = True

    class FakeDateTime:
        @staticmethod
        def now(tz):
            return fake_now

    class FakeHealthState:
        def __init__(self, settings, started_at):
            self.worker_status = "starting"

        def mark_ready(self):
            self.worker_status = "ready"

        def mark_sample_ok(self, timestamp):
            health["ok"] += 1

        def mark_error(self, now):
            health["error"] += 1

        def mark_idle(self):
            health["idle"] = True

    monkeypatch.setattr(service, "setup_runtime_dirs", lambda _settings: None)
    monkeypatch.setattr(service, "TradingCalendar", lambda *args, **kwargs: FakeCalendar())
    monkeypatch.setattr(service, "Storage", FakeStorage)
    monkeypatch.setattr(service, "AkShareDataSource", lambda _settings: object())
    monkeypatch.setattr(service, "WeComClient", lambda webhook_url: {"webhook_url": webhook_url})
    monkeypatch.setattr(service, "AICommentaryClient", lambda _settings: object())
    monkeypatch.setattr(service, "HealthState", FakeHealthState)
    monkeypatch.setattr(service, "start_healthcheck_server", lambda _settings, state: None)
    monkeypatch.setattr(service, "datetime", FakeDateTime)

    def fake_run_once(*args, **kwargs):
        created["run_once_calls"] += 1
        if created["run_once_calls"] == 1:
            return analysis, True
        raise RuntimeError("boom")

    def stop_sleep(seconds):
        if created["run_once_calls"] >= 2:
            raise SystemExit(seconds)

    monkeypatch.setattr(service, "run_once", fake_run_once)
    monkeypatch.setattr(service.time, "sleep", stop_sleep)

    with pytest.raises(SystemExit, match="60"):
        service.run_forever(settings)

    assert created["inited"] is True
    assert health == {"ok": 1, "error": 1}


def test_run_forever_uses_calendar_for_storage_and_sleep(tmp_path: Path, monkeypatch):
    settings = replace(_settings(tmp_path, push_every_sample=False), run_outside_market_hours=False)
    created = {}
    health = {}
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
    monkeypatch.setattr(
        service,
        "start_healthcheck_server",
        lambda _settings, state: health.update(
            {
                "host": _settings.healthcheck_host,
                "port": _settings.healthcheck_port,
                "path": _settings.healthcheck_path,
                "status": state.worker_status,
            }
        ),
    )
    monkeypatch.setattr(service, "datetime", FakeDateTime)

    def stop_sleep(seconds):
        raise RuntimeError(seconds)

    monkeypatch.setattr(service.time, "sleep", stop_sleep)

    with pytest.raises(RuntimeError, match="3600"):
        service.run_forever(settings)

    assert created["db_path"] == settings.db_path
    assert isinstance(created["calendar"], FakeCalendar)
    assert created["inited"] is True
    assert health == {"host": "127.0.0.1", "port": 18080, "path": "/healthz", "status": "starting"}


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


def _analysis(
    timestamp: datetime,
    *,
    score: int = 72,
    band: str = "偏多",
    previous_score: int | None = 60,
    previous_band: str | None = "中性",
    alert_kind: str | None = "strong_long",
) -> MarketAnalysis:
    return MarketAnalysis(
        timestamp=timestamp,
        score=score,
        band=band,
        previous_score=previous_score,
        previous_band=previous_band,
        components={},
        signals={},
        reasons=[],
        warnings=[],
        alert_kind=alert_kind,
    )
