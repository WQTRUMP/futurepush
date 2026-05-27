from __future__ import annotations

from datetime import datetime

from .market_calendar import is_roll_window, is_tail_session
from .metrics import (
    basis,
    basis_bp,
    basis_change_label,
    basis_state,
    clamp,
    classify_price_oi,
    is_dividend_season,
    percentile_rank,
    score_band,
    zscore,
)
from .models import (
    HistoricalProductSnapshot,
    MarketAnalysis,
    MarketSnapshot,
    PRODUCT_CONFIGS,
    PRODUCTS,
    ProductSignal,
    RESONANCE_PRODUCTS,
)


def analyze_market(
    snapshot: MarketSnapshot,
    references: dict[str, HistoricalProductSnapshot | None],
    latest_contracts: dict[str, str],
    previous_score: int | None,
    previous_band: str | None,
    basis_histories: dict[str, list[float]] | None = None,
    dividend_season_adjust: bool = True,
    roll_window_days: int = 7,
) -> MarketAnalysis:
    signals: dict[str, ProductSignal] = {}
    basis_histories = basis_histories or {}
    for product in PRODUCTS:
        future = snapshot.futures.get(product)
        spot = snapshot.spots.get(product)
        if future is None or spot is None:
            continue
        ref = references.get(product)
        item_basis = basis(future.price, spot.price)
        item_basis_bp = basis_bp(future.price, spot.price)
        basis_change_bp = item_basis_bp - ref.basis_bp if ref else None
        history = basis_histories.get(product, [])
        basis_percentile = percentile_rank(history, item_basis_bp)
        basis_zscore = zscore(history, item_basis_bp)
        volume_change = future.volume - ref.volume if ref else None
        oi_change = future.open_interest - ref.open_interest if ref else None
        price_change = future.price - ref.futures_price if ref else None
        previous_contract = latest_contracts.get(product)
        contract_changed = bool(previous_contract and previous_contract != future.contract)
        signals[product] = ProductSignal(
            product=product,
            product_name=PRODUCT_CONFIGS[product].spot_name,
            contract=future.contract,
            previous_contract=previous_contract,
            futures_price=future.price,
            futures_change_pct=future.change_pct,
            spot_price=spot.price,
            spot_change_pct=spot.change_pct,
            basis=item_basis,
            basis_bp=item_basis_bp,
            basis_state=basis_state(item_basis),
            basis_change_bp=basis_change_bp,
            basis_change_label=basis_change_label(basis_change_bp),
            basis_percentile=basis_percentile,
            basis_zscore=basis_zscore,
            basis_history_count=len(history),
            futures_minus_spot_pct=future.change_pct - spot.change_pct,
            volume=future.volume,
            volume_change=volume_change,
            open_interest=future.open_interest,
            open_interest_change=oi_change,
            price_change_5m=price_change,
            price_oi_signal=classify_price_oi(price_change, oi_change),
            main_contract_changed=contract_changed,
        )

    components = {
        "basis_change": _basis_change_component(signals, snapshot.timestamp, dividend_season_adjust),
        "open_interest": _open_interest_component(signals),
        "relative_strength": _relative_strength_component(signals),
        "resonance": _resonance_component(signals),
        "tail": _tail_component(snapshot.timestamp, signals),
    }
    weighted = (
        components["basis_change"] * 0.30
        + components["open_interest"] * 0.25
        + components["relative_strength"] * 0.20
        + components["resonance"] * 0.15
        + components["tail"] * 0.10
    )
    score = int(round(clamp(weighted)))
    band = score_band(score)
    reasons, alert_kind = _reasons_and_alert_kind(
        signals,
        score,
        band,
        previous_band,
        snapshot.timestamp,
        dividend_season_adjust,
        roll_window_days,
    )
    term_summary = _term_summary(snapshot)

    return MarketAnalysis(
        timestamp=snapshot.timestamp,
        score=score,
        band=band,
        previous_score=previous_score,
        previous_band=previous_band,
        components=components,
        signals=signals,
        reasons=reasons,
        warnings=snapshot.warnings,
        alert_kind=alert_kind,
        term_summary=term_summary,
    )


def _basis_change_component(
    signals: dict[str, ProductSignal],
    now: datetime,
    dividend_season_adjust: bool,
) -> float:
    scores = []
    negative_fast_threshold = -5 if dividend_season_adjust and is_dividend_season(now) else -3
    for signal in signals.values():
        change = signal.basis_change_bp
        if change is None:
            scores.append(50.0)
        elif _discount_extreme_repair_signal(signal):
            scores.append(100.0)
        elif change >= 3:
            scores.append(100.0)
        elif change > 0:
            scores.append(70.0)
        elif change <= negative_fast_threshold:
            scores.append(15.0 if dividend_season_adjust and is_dividend_season(now) else 0.0)
        elif change < 0:
            scores.append(30.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _open_interest_component(signals: dict[str, ProductSignal]) -> float:
    scores = []
    for signal in signals.values():
        if signal.price_change_5m is None or signal.open_interest_change is None:
            scores.append(50.0)
        elif signal.price_change_5m > 0 and signal.open_interest_change > 0:
            scores.append(100.0)
        elif signal.price_change_5m > 0 and signal.open_interest_change < 0:
            scores.append(65.0)
        elif signal.price_change_5m < 0 and signal.open_interest_change > 0:
            scores.append(0.0)
        elif signal.price_change_5m < 0 and signal.open_interest_change < 0:
            scores.append(35.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _relative_strength_component(signals: dict[str, ProductSignal]) -> float:
    scores = []
    for signal in signals.values():
        diff = signal.futures_minus_spot_pct
        if diff >= 0.25:
            scores.append(100.0)
        elif diff > 0:
            scores.append(70.0)
        elif diff <= -0.25:
            scores.append(0.0)
        elif diff < 0:
            scores.append(30.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _resonance_component(signals: dict[str, ProductSignal]) -> float:
    tracked = [signals[product] for product in RESONANCE_PRODUCTS if product in signals]
    if not tracked:
        return 50.0
    positives = sum(1 for signal in tracked if signal.futures_change_pct > 0)
    negatives = sum(1 for signal in tracked if signal.futures_change_pct < 0)
    if positives == len(tracked):
        return 100.0
    if positives >= 2:
        return 75.0
    if negatives == len(tracked):
        return 0.0
    if negatives >= 2:
        return 25.0
    return 50.0


def _tail_component(now: datetime, signals: dict[str, ProductSignal]) -> float:
    if not is_tail_session(now):
        return 50.0
    tracked = [signals[product] for product in RESONANCE_PRODUCTS if product in signals]
    if not tracked:
        return 50.0
    scores = []
    for signal in tracked:
        basis_positive = signal.basis_change_bp is not None and signal.basis_change_bp > 0
        oi_positive = signal.open_interest_change is not None and signal.open_interest_change > 0
        price_positive = signal.price_change_5m is not None and signal.price_change_5m > 0
        if basis_positive and oi_positive and price_positive:
            scores.append(100.0)
        elif price_positive and basis_positive:
            scores.append(75.0)
        elif not price_positive and signal.open_interest_change and signal.open_interest_change > 0:
            scores.append(0.0)
        elif signal.basis_change_bp is not None and signal.basis_change_bp < 0:
            scores.append(25.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _reasons_and_alert_kind(
    signals: dict[str, ProductSignal],
    score: int,
    band: str,
    previous_band: str | None,
    now: datetime,
    dividend_season_adjust: bool,
    roll_window_days: int,
) -> tuple[list[str], str | None]:
    reasons: list[str] = []
    alert_kind: str | None = None

    if previous_band and previous_band != band:
        reasons.append(f"评分档位变化: {previous_band} -> {band}")
        alert_kind = "band_change"

    in_roll_window = is_roll_window(now, roll_window_days)
    changed = [
        f"{s.product}:{s.previous_contract or '-'}->{s.contract}"
        for s in signals.values()
        if s.main_contract_changed
    ]
    if changed:
        if in_roll_window:
            reasons.append("交割/换月窗口主力切换: " + ", ".join(changed))
            alert_kind = alert_kind or "rollover"
        else:
            reasons.append("非换月窗口主力合约切换: " + ", ".join(changed))
            alert_kind = alert_kind or "main_contract_change"

    if dividend_season_adjust and is_dividend_season(now):
        reasons.append("5-7月分红季：贴水绝对值降权，优先看贴水收窄/扩大速度")

    repair_products = [signal.product for signal in signals.values() if _discount_extreme_repair_signal(signal)]
    if repair_products:
        reasons.append(f"深贴水低分位后快速收敛: {','.join(repair_products)}")
        alert_kind = "discount_repair"

    worse_products = [signal.product for signal in signals.values() if _discount_extreme_worsening_signal(signal)]
    if worse_products:
        reasons.append(f"深贴水低分位继续扩大: {','.join(worse_products)}")
        alert_kind = alert_kind or "discount_extreme_worse"

    strong_long = _is_strong_long(signals)
    strong_short = _is_strong_short(signals)
    if strong_long:
        reasons.append("强多组合: 上涨 + 增仓 + 基差改善 + IF/IC/IM 共振")
        alert_kind = "strong_long"
    if strong_short:
        reasons.append("强空组合: 下跌 + 增仓 + 贴水扩大 + IM 弱于 IF")
        alert_kind = "strong_short"

    if _is_if_strong_im_weak(signals):
        reasons.append("IF 强、IM 弱，偏权重护盘，小票情绪不足")
        alert_kind = alert_kind or "if_strong_im_weak"

    if _basis_widening_fast(signals, now, dividend_season_adjust):
        reasons.append("贴水快速扩大，期货资金避险增强")
        alert_kind = alert_kind or "basis_widening"

    if alert_kind is None and score >= 80:
        alert_kind = "high_score"
    if alert_kind is None and score <= 19:
        alert_kind = "low_score"

    if is_tail_session(now):
        if score >= 70:
            reasons.append("14:30 后尾盘信号偏多")
        elif score <= 35:
            reasons.append("14:30 后尾盘信号偏空")

    if not reasons:
        reasons.append("无显著异常，按当前评分档位跟踪")

    return reasons, alert_kind


def _is_strong_long(signals: dict[str, ProductSignal]) -> bool:
    tracked = [signals[product] for product in RESONANCE_PRODUCTS if product in signals]
    if len(tracked) < 2:
        return False
    positive_count = sum(1 for signal in tracked if signal.futures_change_pct > 0)
    clean_count = sum(
        1
        for signal in tracked
        if signal.price_change_5m is not None
        and signal.price_change_5m > 0
        and signal.open_interest_change is not None
        and signal.open_interest_change > 0
        and signal.basis_change_bp is not None
        and signal.basis_change_bp > 0
    )
    return positive_count >= 2 and clean_count >= 2


def _is_strong_short(signals: dict[str, ProductSignal]) -> bool:
    tracked = [signals[product] for product in RESONANCE_PRODUCTS if product in signals]
    if len(tracked) < 2:
        return False
    short_count = sum(
        1
        for signal in tracked
        if signal.price_change_5m is not None
        and signal.price_change_5m < 0
        and signal.open_interest_change is not None
        and signal.open_interest_change > 0
        and signal.basis_change_bp is not None
        and signal.basis_change_bp < 0
    )
    return short_count >= 2 and _is_im_weaker_than_if(signals)


def _is_if_strong_im_weak(signals: dict[str, ProductSignal]) -> bool:
    if_signal = signals.get("IF")
    im_signal = signals.get("IM")
    if not if_signal or not im_signal:
        return False
    return if_signal.futures_change_pct > 0 and im_signal.futures_change_pct < 0


def _is_im_weaker_than_if(signals: dict[str, ProductSignal]) -> bool:
    if_signal = signals.get("IF")
    im_signal = signals.get("IM")
    if not if_signal or not im_signal:
        return False
    return if_signal.futures_change_pct - im_signal.futures_change_pct >= 0.3


def _basis_widening_fast(
    signals: dict[str, ProductSignal],
    now: datetime,
    dividend_season_adjust: bool,
) -> bool:
    threshold = -8 if dividend_season_adjust and is_dividend_season(now) else -5
    return any(signal.basis_change_bp is not None and signal.basis_change_bp <= threshold for signal in signals.values())


def _discount_extreme_repair_signal(signal: ProductSignal) -> bool:
    return (
        signal.basis_bp < 0
        and signal.basis_percentile is not None
        and signal.basis_percentile <= 0.10
        and signal.basis_change_bp is not None
        and signal.basis_change_bp >= 3
    )


def _discount_extreme_worsening_signal(signal: ProductSignal) -> bool:
    return (
        signal.basis_bp < 0
        and signal.basis_percentile is not None
        and signal.basis_percentile <= 0.10
        and signal.basis_change_bp is not None
        and signal.basis_change_bp <= -3
    )


def _term_summary(snapshot: MarketSnapshot) -> dict[str, str]:
    result: dict[str, str] = {}
    for product, terms in snapshot.terms.items():
        if not terms:
            continue
        parts = []
        for term in terms[:4]:
            if term.basis_bp is None:
                parts.append(f"{term.contract}:n/a")
            else:
                parts.append(f"{term.contract}:{term.basis_bp:+.1f}bp({basis_state(term.basis or 0)})")
        result[product] = " ".join(parts)
    return result


def _average(values: list[float]) -> float:
    if not values:
        return 50.0
    return sum(values) / len(values)
