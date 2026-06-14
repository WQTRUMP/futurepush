from datetime import datetime
from zoneinfo import ZoneInfo

from futures_signal.models import FutureQuote, HistoricalProductSnapshot, MarketSnapshot, PositionRankSignal, SpotQuote
from futures_signal.scoring import analyze_market


TZ = ZoneInfo("Asia/Shanghai")


def _snapshot(now, futures_price_delta=20, oi_delta=100, basis_improves=True, all_positive=True):
    futures = {}
    spots = {}
    refs = {}
    base_prices = {"IF": 4000.0, "IH": 2800.0, "IC": 6000.0, "IM": 6200.0}
    for product, spot_price in base_prices.items():
        prev_future = spot_price - 10
        current_spot = spot_price + 5
        current_future = prev_future + futures_price_delta
        if not basis_improves:
            current_future = current_spot - 30
        change_pct = 0.8 if all_positive else -0.8
        if not all_positive:
            current_future = prev_future - abs(futures_price_delta)
        futures[product] = FutureQuote(
            product=product,
            contract=f"{product}2606",
            name=product,
            price=current_future,
            change_pct=change_pct,
            volume=10000 + oi_delta,
            open_interest=20000 + oi_delta,
            tick_time=now,
        )
        spots[product] = SpotQuote(
            product=product,
            index_code="000000",
            name=product,
            price=current_spot,
            change_pct=0.1 if all_positive else -0.1,
            volume=None,
            amount=None,
            tick_time=now,
        )
        refs[product] = HistoricalProductSnapshot(
            timestamp=now,
            product=product,
            contract=f"{product}2606",
            futures_price=prev_future,
            spot_price=spot_price,
            basis_bp=(prev_future - spot_price) / spot_price * 10000,
            volume=10000,
            open_interest=20000,
        )
    return MarketSnapshot(timestamp=now, futures=futures, spots=spots), refs


def _positive_basis_histories():
    return {product: [-20.0 + index for index in range(20)] for product in ("IF", "IH", "IC", "IM")}


def test_strong_long_scores_high():
    now = datetime(2026, 5, 27, 14, 40, tzinfo=TZ)
    snapshot, refs = _snapshot(now)
    analysis = analyze_market(snapshot, refs, {}, None, None, basis_histories=_positive_basis_histories())
    assert analysis.score >= 70
    assert analysis.alert_kind == "strong_long"


def test_strong_short_scores_low():
    now = datetime(2026, 5, 27, 14, 40, tzinfo=TZ)
    snapshot, refs = _snapshot(now, futures_price_delta=20, oi_delta=100, basis_improves=False, all_positive=False)
    analysis = analyze_market(snapshot, refs, {}, None, None)
    assert analysis.score <= 35
    assert analysis.band in {"偏空", "明显空头"}


def test_band_change_alert():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=TZ)
    snapshot, refs = _snapshot(now)
    analysis = analyze_market(snapshot, refs, {}, 45, "中性震荡")
    assert any(reason.startswith("评分档位变化: 中性震荡 ->") for reason in analysis.reasons)


def test_daily_open_interest_change_is_calculated():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=TZ)
    snapshot, refs = _snapshot(now)
    daily_refs = {
        product: HistoricalProductSnapshot(
            timestamp=datetime(2026, 5, 26, 15, 0, tzinfo=TZ),
            product=product,
            contract=f"{product}2606",
            futures_price=refs[product].futures_price - 5,
            spot_price=refs[product].spot_price,
            basis_bp=refs[product].basis_bp - 2,
            volume=9000,
            open_interest=19000,
        )
        for product in refs
    }

    analysis = analyze_market(snapshot, refs, {}, None, None, daily_references=daily_refs)

    signal = analysis.signals["IF"]
    assert signal.daily_open_interest_change == signal.open_interest - 19000
    assert signal.daily_price_change == signal.futures_price - daily_refs["IF"].futures_price
    assert round(signal.daily_basis_change_bp, 6) == round(signal.basis_bp - daily_refs["IF"].basis_bp, 6)


def test_net_short_change_is_carried_into_signal_and_score():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=TZ)
    snapshot, refs = _snapshot(now)
    snapshot = MarketSnapshot(
        timestamp=snapshot.timestamp,
        futures=snapshot.futures,
        spots=snapshot.spots,
        positions={
            "IM": PositionRankSignal("IM", net_short_top20=3000, net_short_change_top20=1500, citic_net_short_change=749),
            "IC": PositionRankSignal("IC", net_short_top20=2000, net_short_change_top20=1300),
            "IH": PositionRankSignal("IH", net_short_top20=1000, net_short_change_top20=500),
            "IF": PositionRankSignal("IF", net_short_top20=-1000, net_short_change_top20=-1200),
        },
    )

    analysis = analyze_market(snapshot, refs, {}, None, None)

    assert analysis.signals["IM"].net_short_change_top20 == 1500
    assert analysis.signals["IM"].citic_net_short_change == 749
    assert "前20净空扩大: IC,IM" in analysis.reasons
    assert analysis.components["position_rank"] < 50


def test_contract_mismatch_disables_intraday_and_daily_diffs():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=TZ)
    snapshot, refs = _snapshot(now)
    refs["IF"] = HistoricalProductSnapshot(
        timestamp=now,
        product="IF",
        contract="IF2605",
        futures_price=3900,
        spot_price=3990,
        basis_bp=-10,
        volume=1000,
        open_interest=1000,
    )
    daily_refs = {"IF": refs["IF"]}

    analysis = analyze_market(snapshot, refs, {}, None, None, daily_references=daily_refs)

    signal = analysis.signals["IF"]
    assert signal.main_contract_changed is True
    assert signal.price_change_5m is None
    assert signal.open_interest_change is None
    assert signal.basis_change_bp is None
    assert signal.daily_price_change is None
    assert signal.daily_open_interest_change is None
    assert "IF 5m参考合约不一致，已禁用短线差分" in analysis.warnings


def test_strong_long_requires_future_stronger_than_spot():
    now = datetime(2026, 5, 27, 14, 40, tzinfo=TZ)
    snapshot, refs = _snapshot(now)
    futures = {
        product: quote.__class__(
            product=quote.product,
            contract=quote.contract,
            name=quote.name,
            price=quote.price,
            change_pct=0.2,
            volume=quote.volume,
            open_interest=quote.open_interest,
            tick_time=quote.tick_time,
        )
        for product, quote in snapshot.futures.items()
    }
    spots = {
        product: quote.__class__(
            product=quote.product,
            index_code=quote.index_code,
            name=quote.name,
            price=quote.price,
            change_pct=0.5,
            volume=quote.volume,
            amount=quote.amount,
            tick_time=quote.tick_time,
        )
        for product, quote in snapshot.spots.items()
    }
    weak_snapshot = MarketSnapshot(timestamp=now, futures=futures, spots=spots)

    analysis = analyze_market(weak_snapshot, refs, {}, None, None)

    assert analysis.alert_kind != "strong_long"


def test_strong_long_requires_positive_lead_residual_even_when_futures_rise():
    now = datetime(2026, 5, 27, 14, 40, tzinfo=TZ)
    snapshot, refs = _snapshot(now)
    futures = {
        product: quote.__class__(
            product=quote.product,
            contract=quote.contract,
            name=quote.name,
            price=refs[product].futures_price * 1.001,
            change_pct=0.5,
            volume=quote.volume,
            open_interest=quote.open_interest,
            tick_time=quote.tick_time,
        )
        for product, quote in snapshot.futures.items()
    }
    spots = {
        product: quote.__class__(
            product=quote.product,
            index_code=quote.index_code,
            name=quote.name,
            price=refs[product].spot_price * 1.004,
            change_pct=0.2,
            volume=quote.volume,
            amount=quote.amount,
            tick_time=quote.tick_time,
        )
        for product, quote in snapshot.spots.items()
    }
    weak_residual_snapshot = MarketSnapshot(timestamp=now, futures=futures, spots=spots)

    analysis = analyze_market(
        weak_residual_snapshot,
        refs,
        {},
        None,
        None,
        basis_histories=_positive_basis_histories(),
    )

    assert all(signal.lead_residual_5m_pct < 0 for signal in analysis.signals.values())
    assert analysis.alert_kind != "strong_long"


def test_tiny_open_interest_change_is_not_full_score():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=TZ)
    snapshot, refs = _snapshot(now, oi_delta=1)

    analysis = analyze_market(snapshot, refs, {}, None, None)

    assert analysis.components["open_interest"] < 100
