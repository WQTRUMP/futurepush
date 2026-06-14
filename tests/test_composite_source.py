from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import futures_signal.composite_source as composite_module
from futures_signal.akshare_providers import ProviderObservation, RealtimeQuoteBundleProvider
from futures_signal.composite_source import CompositeMarketDataSource
from futures_signal.config import Settings
from futures_signal.data_sources import DataSourceError
from futures_signal.market_calendar import TradingCalendar
from futures_signal.models import PRODUCT_CONFIGS


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


class _FakeProvider:
    def __init__(self, payload, observation):
        self.payload = payload
        self.observation = observation

    def fetch(self, now, warnings):
        return self.payload, self.observation


def _source(tmp_path: Path) -> CompositeMarketDataSource:
    settings = _settings(tmp_path)
    quote_bundle_provider = RealtimeQuoteBundleProvider(
        client=type("Client", (), {"futures_realtime_rows": lambda self, symbol: []})()
    )
    return CompositeMarketDataSource(
        settings=settings,
        ak=type("FakeAk", (), {})(),
        quote_bundle_provider=quote_bundle_provider,
        calendar=TradingCalendar(settings.tz, use_akshare=False, cache_path=settings.trade_calendar_cache_path),
    )


def test_fetch_builds_snapshot_and_tracks_observations(tmp_path, monkeypatch):
    source = _source(tmp_path)
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    class FakeDateTime:
        @staticmethod
        def now(tz):
            return now

    monkeypatch.setattr(composite_module, "datetime", FakeDateTime)

    futures = {product: type("Future", (), {"product": product})() for product in PRODUCT_CONFIGS}
    spots = {product: type("Spot", (), {"product": product})() for product in PRODUCT_CONFIGS}
    main_bundle = {}
    term_bundle = {}

    def fake_main(current, warnings, bundle=None):
        main_bundle["value"] = bundle
        return futures

    def fake_terms(current, current_spots, warnings, bundle=None):
        term_bundle["value"] = bundle
        return {}

    monkeypatch.setattr(source, "_fetch_main_futures", fake_main)
    monkeypatch.setattr(source, "_fetch_spots", lambda current, warnings: spots)
    monkeypatch.setattr(source, "_fetch_terms_if_due", fake_terms)
    source.position_rank_provider = _FakeProvider(
        {"IF": object()},
        ProviderObservation.degraded("positions", {"fallback": True, "as_of_date": "20260527"}),
    )
    source.position_trend_provider = _FakeProvider(
        {"IF": object()},
        ProviderObservation.ok("position_trends", {"cache_hit": False, "days": 5}),
    )

    snapshot = source.fetch()

    assert snapshot.timestamp == now
    assert snapshot.source == "akshare"
    assert main_bundle["value"] is term_bundle["value"]
    observations = {item.provider: item for item in source.last_fetch_observation.observations}
    assert observations["positions"].status == "degraded"
    assert observations["positions"].details["fallback"] is True
    assert observations["position_trends"].status == "ok"


def test_fetch_warns_or_raises_for_missing_products(tmp_path, monkeypatch):
    source = _source(tmp_path)
    monkeypatch.setattr(source, "_fetch_main_futures", lambda current, warnings, bundle=None: {"IF": object()})
    monkeypatch.setattr(source, "_fetch_spots", lambda current, warnings: {"IF": object(), "IH": object(), "IC": object()})
    monkeypatch.setattr(source, "_fetch_terms_if_due", lambda current, current_spots, warnings, bundle=None: {})
    source.position_rank_provider = _FakeProvider({}, ProviderObservation.ok("positions"))
    source.position_trend_provider = _FakeProvider({}, ProviderObservation.skipped("position_trends"))

    snapshot = source.fetch()
    assert "部分品种缺失: IH,IC,IM" in snapshot.warnings

    monkeypatch.setattr(source, "_fetch_main_futures", lambda current, warnings, bundle=None: {})
    with pytest.raises(DataSourceError, match="期货主力行情"):
        source.fetch()

    monkeypatch.setattr(source, "_fetch_main_futures", lambda current, warnings, bundle=None: {"IF": object()})
    monkeypatch.setattr(source, "_fetch_spots", lambda current, warnings: {})
    with pytest.raises(DataSourceError, match="现货指数行情"):
        source.fetch()
