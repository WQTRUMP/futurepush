from datetime import datetime
from zoneinfo import ZoneInfo

from futures_signal.models import FutureQuote, HistoricalProductSnapshot, MarketSnapshot, SpotQuote
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


def test_strong_long_scores_high():
    now = datetime(2026, 5, 27, 14, 40, tzinfo=TZ)
    snapshot, refs = _snapshot(now)
    analysis = analyze_market(snapshot, refs, {}, None, None)
    assert analysis.score >= 80
    assert analysis.band == "期现共振偏多"
    assert analysis.alert_kind == "strong_long"


def test_strong_short_scores_low():
    now = datetime(2026, 5, 27, 14, 40, tzinfo=TZ)
    snapshot, refs = _snapshot(now, futures_price_delta=20, oi_delta=100, basis_improves=False, all_positive=False)
    analysis = analyze_market(snapshot, refs, {}, None, None)
    assert analysis.score <= 20
    assert analysis.band == "明显空头"


def test_band_change_alert():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=TZ)
    snapshot, refs = _snapshot(now)
    analysis = analyze_market(snapshot, refs, {}, 45, "中性震荡")
    assert "评分档位变化: 中性震荡 -> 期现共振偏多" in analysis.reasons
