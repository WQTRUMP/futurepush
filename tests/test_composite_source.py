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


def test_observation_helpers_and_logging_cover_degraded_failed_and_skipped_states(tmp_path, monkeypatch, caplog):
    source = _source(tmp_path)

    _, failed = source._observe_provider("main_futures", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert failed.status == "failed"
    assert failed.details == {"error": "RuntimeError"}

    partial, degraded = source._observe_provider("main_futures", lambda: {"IF": object()})
    assert partial == {"IF": object()} or "IF" in partial
    assert degraded.status == "degraded"

    assert source._status_for("spots", {}, {"missing_products": []}).status == "failed"

    source.settings = Settings(**{**source.settings.__dict__, "fetch_term_structure": False})
    skipped = source._status_for("terms", {"IF": []}, source._provider_details("terms", {"IF": []}))
    assert skipped.status == "skipped"
    assert skipped.details["cache_hit"] is False
    assert source._provider_details("unknown", {}) == {}
    assert source._status_for("unknown", {"value": 1}, {}).status == "ok"

    caplog.set_level("DEBUG")
    source._log_observation(
        composite_module.FetchObservation(
            observations=[
                ProviderObservation.ok("spots", {"products": 4}).with_duration(1.0),
                ProviderObservation.degraded("positions", {"fallback": True}).with_duration(2.0),
            ]
        )
    )
    assert "provider=spots status=ok" in caplog.text
    assert "provider=positions status=degraded" in caplog.text


def test_fetch_main_futures_covers_contract_fallback_and_batch_supplement(tmp_path, monkeypatch):
    source = _source(tmp_path)
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    warnings: list[str] = []

    monkeypatch.setattr(source, "_main_contract_symbols", lambda current_warnings: [])
    monkeypatch.setattr(source, "_fetch_main_futures_from_realtime", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        source.ak,
        "futures_zh_spot",
        lambda symbol, market, adjust: [
            {"name": "mystery", "trade": 4010, "ticktime": "10:01:02"},
            {"symbol": "XX0", "trade": 2800, "ticktime": "10:01:02"},
            {"symbol": "IC0", "trade": 0, "ticktime": "10:01:02"},
            {"symbol": "IM0", "trade": 6100, "ticktime": "10:01:02"},
            {"name": "still-unknown", "trade": 1, "ticktime": "10:01:02"},
        ],
        raising=False,
    )

    futures = source._fetch_main_futures(now, warnings)

    assert sorted(futures) == ["IF", "IH", "IM"]
    assert any("已回退到新浪连续合约" in item for item in warnings)

    monkeypatch.setattr(source, "_main_contract_symbols", lambda current_warnings: ["IF2606", "IH2606", "IC2606", "IM2606"])
    monkeypatch.setattr(
        source,
        "_fetch_main_futures_from_realtime",
        lambda current, contracts, current_warnings, bundle=None: {product: object() for product in PRODUCT_CONFIGS},
    )
    monkeypatch.setattr(
        source.ak,
        "futures_zh_spot",
        lambda symbol, market, adjust: (_ for _ in ()).throw(AssertionError("batch fallback should not run")),
        raising=False,
    )
    assert sorted(source._fetch_main_futures(now, []).keys()) == ["IC", "IF", "IH", "IM"]


def test_fetch_main_futures_handles_batch_failures_and_empty_rows(tmp_path, monkeypatch):
    source = _source(tmp_path)
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    monkeypatch.setattr(source, "_main_contract_symbols", lambda current_warnings: ["IF2606"])
    monkeypatch.setattr(source, "_fetch_main_futures_from_realtime", lambda *args, **kwargs: {})

    warnings: list[str] = []
    monkeypatch.setattr(
        source.ak,
        "futures_zh_spot",
        lambda symbol, market, adjust: (_ for _ in ()).throw(RuntimeError("spot down")),
        raising=False,
    )
    assert source._fetch_main_futures(now, warnings) == {}
    assert any("新浪批量补充源获取失败" in item for item in warnings)

    warnings.clear()
    monkeypatch.setattr(source.ak, "futures_zh_spot", lambda symbol, market, adjust: [], raising=False)
    assert source._fetch_main_futures(now, warnings) == {}
    assert warnings[-1] == "主力期货实时源和新浪批量补充源均返回空数据"


def test_realtime_future_helpers_cover_preferred_contract_and_invalid_rows(tmp_path, monkeypatch):
    source = _source(tmp_path)
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    warnings: list[str] = []

    realtime_map = {
        PRODUCT_CONFIGS["IF"].future_name: [
            {"symbol": "IF2609", "trade": 4012, "ticktime": "10:00:00"},
            {"symbol": "IF2606", "trade": 4010, "ticktime": "10:00:00"},
        ],
        PRODUCT_CONFIGS["IC"].future_name: [{"symbol": "IC2606", "trade": 0, "ticktime": "10:00:00"}],
        PRODUCT_CONFIGS["IM"].future_name: [],
    }

    def fake_realtime_rows(symbol, bundle=None):
        if symbol == PRODUCT_CONFIGS["IH"].future_name:
            raise RuntimeError("down")
        return realtime_map[symbol]

    monkeypatch.setattr(source, "_realtime_rows", fake_realtime_rows)

    result = source._fetch_main_futures_from_realtime(now, ["IF2606", "IH2606", "IC2606", "IM2606"], warnings)

    assert result["IF"].contract == "IF2606"
    assert any("IH 逐品种期货实时源获取失败" in item for item in warnings)
    assert any("IC 逐品种期货实时源价格无效" in item for item in warnings)
    assert any("IM 逐品种期货实时源未找到主力合约" in item for item in warnings)
    assert source._select_main_future_row("IF", realtime_map[PRODUCT_CONFIGS["IF"].future_name], "IF2606")["symbol"] == "IF2606"
    assert source._select_main_future_row("IF", [{"symbol": "IF2609"}], None)["symbol"] == "IF2609"
    assert source._select_main_future_row("IF", [{"symbol": "沪深300指数期货"}], None)["symbol"] == "沪深300指数期货"
    assert source._select_main_future_row("IF", [], None) is None


def test_main_contract_spot_and_term_helpers_cover_fallback_paths(tmp_path, monkeypatch):
    source = _source(tmp_path)
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    warnings: list[str] = []
    monkeypatch.setattr(source.ak, "match_main_contract", lambda symbol: "IF2606, IH2606 IC2606，IM2606 BAD", raising=False)
    assert source._main_contract_symbols(warnings) == ["IF2606", "IH2606", "IC2606", "IM2606"]
    monkeypatch.setattr(source.ak, "match_main_contract", lambda symbol: ["IF2606", "BAD"], raising=False)
    assert source._main_contract_symbols(warnings) == ["IF2606"]
    monkeypatch.setattr(source.ak, "match_main_contract", lambda symbol: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)
    assert source._main_contract_symbols(warnings) == []
    assert any("match_main_contract 调用失败" in item for item in warnings)

    wanted = {config.spot_code: product for product, config in PRODUCT_CONFIGS.items()}
    monkeypatch.setattr(
        source.ak,
        "stock_zh_index_spot_sina",
        lambda: [
            {"代码": PRODUCT_CONFIGS["IF"].spot_code, "名称": "沪深300", "最新价": 3999, "涨跌幅": 0.2, "时间": "10:00:00"},
            {"代码": "000000", "名称": "ignore", "最新价": 1, "涨跌幅": 0.0, "时间": "10:00:00"},
        ],
        raising=False,
    )
    em_calls: list[str] = []

    def fake_em(symbol):
        em_calls.append(symbol)
        if symbol == "中证系列指数":
            return [
                {"代码": PRODUCT_CONFIGS["IC"].spot_code, "名称": "中证500", "最新价": 5900, "涨跌幅": 0.3, "时间": "10:00:00"},
                {"代码": PRODUCT_CONFIGS["IM"].spot_code, "名称": "中证1000", "最新价": 6100, "涨跌幅": 0.4, "时间": "10:00:00"},
            ]
        return [
            {"代码": PRODUCT_CONFIGS["IH"].spot_code, "名称": "上证50", "最新价": 2800, "涨跌幅": 0.1, "时间": "10:00:00"}
        ]

    monkeypatch.setattr(source.ak, "stock_zh_index_spot_em", fake_em, raising=False)
    warnings.clear()
    spots = source._fetch_spots(now, warnings)
    assert sorted(spots) == ["IC", "IF", "IH", "IM"]
    assert any("现货指数已使用东方财富补充源" in item for item in warnings)
    assert em_calls
    warnings.clear()
    fallback_spots = source._fetch_spots_from_sina(now, wanted, warnings)
    assert sorted(fallback_spots) == ["IF"]
    assert warnings == ["现货指数已使用新浪备用源"]

    warnings.clear()
    monkeypatch.setattr(
        source.ak,
        "stock_zh_index_spot_sina",
        lambda: (_ for _ in ()).throw(RuntimeError("sina down")),
        raising=False,
    )
    monkeypatch.setattr(
        source.ak,
        "stock_zh_index_spot_em",
        lambda symbol: (_ for _ in ()).throw(RuntimeError(f"{symbol} down")),
        raising=False,
    )
    assert source._fetch_spots(now, warnings) == {}
    assert any("现货指数缺失" in item for item in warnings)

    source.settings = Settings(**{**source.settings.__dict__, "fetch_term_structure": False})
    assert source._fetch_terms_if_due(now, {}, [], bundle=None) == {}


def test_term_and_datetime_helpers_cover_cache_and_parsing(tmp_path, monkeypatch):
    source = _source(tmp_path)
    now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    warnings: list[str] = []
    spots = {"IF": type("Spot", (), {"price": 4000.0})()}

    calls = {"count": 0}

    def fake_rows(symbol, bundle=None):
        calls["count"] += 1
        if symbol == PRODUCT_CONFIGS["IF"].future_name:
            return [
                {"symbol": "IH2606", "trade": 2800, "ticktime": "10:01:02"},
                {"symbol": "IF2606", "trade": 4010, "volume": 10, "position": 20, "ticktime": "10:01:02"},
                {"symbol": "IF2607", "trade": 4020, "volume": 8, "position": 18, "ticktime": "10:01"},
                {"symbol": "IF2608", "trade": 0, "ticktime": "10:01:02"},
                {"symbol": "IF88", "trade": 3990, "ticktime": "10:01:02"},
            ]
        if symbol == PRODUCT_CONFIGS["IH"].future_name:
            raise RuntimeError("ih down")
        return []

    monkeypatch.setattr(source, "_realtime_rows", fake_rows)
    terms = source._fetch_terms_if_due(now, spots, warnings)
    assert len(terms["IF"]) == 2
    assert terms["IF"][0].basis == 10.0
    assert any("IH 期限结构获取失败" in item for item in warnings)

    cached = source._fetch_terms_if_due(now, spots, warnings)
    assert cached is source._last_terms
    assert calls["count"] == len(PRODUCT_CONFIGS)

    assert source._parse_tick_time(now, {"ticktime": "10:02:03"}).hour == 10
    assert source._parse_tick_time(now, {}) is None
    assert source._parse_spot_tick_time(now, {"时间": "10:02"}).minute == 2
    assert source._parse_spot_tick_time(now, {}) is None
    assert source._parse_datetime_value("2026-05-28 10:02:03", "2026-05-28").second == 3
    assert source._parse_datetime_value("20260528 10:02", "20260528").minute == 2
    assert source._parse_datetime_value("2026-05-28T02:00:00+00:00", "2026-05-28").hour == 10
    assert source._parse_datetime_value("10:02:03", "2026-05-28").hour == 10
    assert source._parse_datetime_value("10:02", "20260528").minute == 2
    assert source._parse_datetime_value("", "2026-05-28") is None
    assert source._parse_datetime_value("bad", "2026-05-28") is None

    assert source._change_pct({"changepercent": 1.5}, 100) == 1.5
    assert round(source._change_pct({"preclose": 100}, 110), 2) == 10.0
    assert source._change_pct({}, 110) == 0.0
