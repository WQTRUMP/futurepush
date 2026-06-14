from __future__ import annotations

from datetime import datetime, time

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

TAIL_WEIGHT_START = time(14, 30)


def analyze_market(
    snapshot: MarketSnapshot,
    references: dict[str, HistoricalProductSnapshot | None],
    latest_contracts: dict[str, str],
    previous_score: int | None,
    previous_band: str | None,
    basis_histories: dict[str, list[float]] | None = None,
    daily_references: dict[str, HistoricalProductSnapshot | None] | None = None,
    dividend_season_adjust: bool = True,
    roll_window_days: int = 7,
) -> MarketAnalysis:
    signals: dict[str, ProductSignal] = {}
    warnings = list(snapshot.warnings)
    basis_histories = basis_histories or {}
    daily_references = daily_references or {}
    for product in PRODUCTS:
        future = snapshot.futures.get(product)
        spot = snapshot.spots.get(product)
        if future is None or spot is None:
            continue
        ref = references.get(product)
        item_basis = basis(future.price, spot.price)
        item_basis_bp = basis_bp(future.price, spot.price)
        ref_same_contract = ref is not None and ref.contract == future.contract
        basis_change_bp = item_basis_bp - ref.basis_bp if ref_same_contract else None
        history = basis_histories.get(product, [])
        basis_percentile = percentile_rank(history, item_basis_bp)
        basis_zscore = zscore(history, item_basis_bp)
        volume_change = future.volume - ref.volume if ref_same_contract else None
        oi_change = future.open_interest - ref.open_interest if ref_same_contract else None
        price_change = future.price - ref.futures_price if ref_same_contract else None
        futures_return_5m = _return_pct(future.price, ref.futures_price) if ref_same_contract else None
        spot_return_5m = _return_pct(spot.price, ref.spot_price) if ref_same_contract else None
        lead_beta = PRODUCT_CONFIGS[product].lead_beta
        lead_residual_5m = (
            futures_return_5m - lead_beta * spot_return_5m
            if futures_return_5m is not None and spot_return_5m is not None
            else None
        )
        daily_ref = daily_references.get(product)
        daily_ref_same_contract = daily_ref is not None and daily_ref.contract == future.contract
        daily_price_change = future.price - daily_ref.futures_price if daily_ref_same_contract else None
        daily_oi_change = future.open_interest - daily_ref.open_interest if daily_ref_same_contract else None
        daily_basis_change_bp = item_basis_bp - daily_ref.basis_bp if daily_ref_same_contract else None
        previous_contract = latest_contracts.get(product)
        contract_changed = bool(
            (previous_contract and previous_contract != future.contract)
            or (ref and ref.contract != future.contract)
            or (daily_ref and daily_ref.contract != future.contract)
        )
        if ref and ref.contract != future.contract:
            warnings.append(f"{product} 5m参考合约不一致，已禁用短线差分")
        if daily_ref and daily_ref.contract != future.contract:
            warnings.append(f"{product} 日线参考合约不一致，已禁用日线差分")
        position = snapshot.positions.get(product)
        net_short_change = position.net_short_change_top20 if position else None
        citic_net_short_change = position.citic_net_short_change if position else None
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
            lead_beta=lead_beta,
            futures_return_5m_pct=futures_return_5m,
            spot_return_5m_pct=spot_return_5m,
            lead_residual_5m_pct=lead_residual_5m,
            volume=future.volume,
            volume_change=volume_change,
            volume_change_ratio=_ratio(volume_change, future.volume),
            open_interest=future.open_interest,
            open_interest_change=oi_change,
            open_interest_change_ratio=_ratio(oi_change, future.open_interest),
            price_change_5m=price_change,
            price_oi_signal=classify_price_oi(price_change, oi_change),
            main_contract_changed=contract_changed,
            daily_price_change=daily_price_change,
            daily_open_interest_change=daily_oi_change,
            daily_open_interest_change_ratio=_ratio(daily_oi_change, future.open_interest),
            daily_basis_change_bp=daily_basis_change_bp,
            net_short_change_top20=net_short_change,
            net_short_change_top20_ratio=_ratio(net_short_change, future.open_interest),
            citic_net_short_change=citic_net_short_change,
            citic_net_short_change_ratio=_ratio(citic_net_short_change, future.open_interest),
            position_rank_lag_days=position.lag_days if position else None,
            position_rank_is_fallback=position.is_fallback if position else False,
        )

    components = {
        "lead_residual": _lead_residual_component(signals),
        "tail_lead_residual": _tail_lead_residual_component(snapshot.timestamp, signals),
        "basis_change": _basis_change_component(signals, snapshot.timestamp, dividend_season_adjust),
        "open_interest": _open_interest_component(signals),
        "price_oi_volume": _price_oi_volume_component(signals),
        "position_rank": _position_rank_component(signals),
        "position_confirm": _position_confirm_component(signals),
        "relative_strength": _relative_strength_component(signals),
        "resonance": _resonance_component(signals),
        "spot_breadth": _spot_breadth_component(signals),
        "daily_structure": _daily_structure_component(signals),
        "daily_basis_structure": _daily_basis_structure_component(signals),
        "term_structure": _term_structure_component(snapshot),
        "tail": _tail_component(snapshot.timestamp, signals),
        "external_risk": 50.0,
    }
    weighted = _weighted_score(components, score_weights(snapshot.timestamp))
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
        warnings=warnings,
        alert_kind=alert_kind,
        term_summary=term_summary,
        position_trends=snapshot.position_trends,
        fetched_at=snapshot.fetched_at or snapshot.timestamp,
        source=snapshot.source,
        valid_for_scoring=snapshot.valid_for_scoring,
    )


def score_weights(now: datetime) -> dict[str, float]:
    current = now.time()
    if current >= TAIL_WEIGHT_START:
        return {
            "tail_lead_residual": 0.25,
            "daily_basis_structure": 0.20,
            "position_confirm": 0.20,
            "resonance": 0.15,
            "spot_breadth": 0.10,
            "external_risk": 0.10,
        }
    return {
        "lead_residual": 0.30,
        "basis_change": 0.25,
        "price_oi_volume": 0.20,
        "resonance": 0.15,
        "spot_breadth": 0.10,
    }


def _weighted_score(components: dict[str, float], weights: dict[str, float]) -> float:
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return 50.0
    return sum(components.get(name, 50.0) * weight for name, weight in weights.items()) / total_weight


def _lead_residual_component(signals: dict[str, ProductSignal]) -> float:
    scores = []
    for signal in signals.values():
        residual = signal.lead_residual_5m_pct
        if residual is None:
            scores.append(50.0)
        elif residual >= 0.08:
            scores.append(100.0)
        elif residual > 0:
            scores.append(70.0)
        elif residual <= -0.08:
            scores.append(0.0)
        elif residual < 0:
            scores.append(30.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _tail_lead_residual_component(now: datetime, signals: dict[str, ProductSignal]) -> float:
    if not is_tail_session(now):
        return 50.0
    return _lead_residual_component(signals)


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
        ratio = signal.open_interest_change_ratio
        if signal.price_change_5m is None or ratio is None:
            scores.append(50.0)
        elif signal.price_change_5m > 0 and ratio >= 0.003:
            scores.append(100.0)
        elif signal.price_change_5m > 0 and ratio > 0:
            scores.append(75.0)
        elif signal.price_change_5m > 0 and ratio < 0:
            scores.append(60.0)
        elif signal.price_change_5m < 0 and ratio >= 0.003:
            scores.append(0.0)
        elif signal.price_change_5m < 0 and ratio > 0:
            scores.append(20.0)
        elif signal.price_change_5m < 0 and ratio < 0:
            scores.append(35.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _price_oi_volume_component(signals: dict[str, ProductSignal]) -> float:
    scores = []
    for signal in signals.values():
        residual = signal.lead_residual_5m_pct
        basis_change = signal.basis_change_bp
        oi_ratio = signal.open_interest_change_ratio
        volume_ratio = signal.volume_change_ratio
        if signal.price_change_5m is None or oi_ratio is None or basis_change is None:
            scores.append(50.0)
            continue

        volume_bonus = 5.0 if volume_ratio is not None and volume_ratio > 0.02 else 0.0
        if signal.price_change_5m > 0 and oi_ratio > 0 and basis_change > 0 and (residual is None or residual > 0):
            scores.append(min(100.0, 95.0 + volume_bonus))
        elif signal.price_change_5m > 0 and oi_ratio < 0:
            scores.append(60.0)
        elif signal.price_change_5m > 0 and basis_change <= 0:
            scores.append(45.0)
        elif signal.price_change_5m < 0 and oi_ratio > 0 and basis_change < 0 and (residual is None or residual < 0):
            scores.append(max(0.0, 5.0 - volume_bonus))
        elif signal.price_change_5m < 0 and oi_ratio < 0:
            scores.append(35.0)
        elif signal.price_change_5m < 0 and basis_change >= 0:
            scores.append(55.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _position_rank_component(signals: dict[str, ProductSignal]) -> float:
    scores = []
    for signal in signals.values():
        ratio = signal.net_short_change_top20_ratio
        if ratio is None:
            scores.append(50.0)
        elif ratio <= -0.05:
            scores.append(100.0)
        elif ratio < -0.01:
            scores.append(70.0)
        elif ratio >= 0.05:
            scores.append(0.0)
        elif ratio > 0.01:
            scores.append(30.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _position_confirm_component(signals: dict[str, ProductSignal]) -> float:
    oi_score = _open_interest_component(signals)
    rank_score = _position_rank_component(signals)
    return oi_score * 0.45 + rank_score * 0.55


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


def _spot_breadth_component(signals: dict[str, ProductSignal]) -> float:
    if not signals:
        return 50.0
    positives = sum(1 for signal in signals.values() if signal.spot_change_pct > 0)
    negatives = sum(1 for signal in signals.values() if signal.spot_change_pct < 0)
    if positives == len(signals):
        return 80.0
    if positives >= 3:
        return 65.0
    if negatives == len(signals):
        return 20.0
    if negatives >= 3:
        return 35.0
    return 50.0


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


def _daily_structure_component(signals: dict[str, ProductSignal]) -> float:
    scores = []
    for signal in signals.values():
        price = signal.daily_price_change
        oi_ratio = signal.daily_open_interest_change_ratio
        basis_change = signal.daily_basis_change_bp
        if price is None or oi_ratio is None or basis_change is None:
            scores.append(50.0)
        elif price > 0 and oi_ratio > 0 and basis_change > 0 and signal.futures_minus_spot_pct > 0:
            scores.append(100.0 if oi_ratio >= 0.005 else 80.0)
        elif price < 0 and oi_ratio > 0 and (basis_change < 0 or signal.futures_minus_spot_pct < 0):
            scores.append(0.0 if oi_ratio >= 0.005 else 20.0)
        elif oi_ratio > 0 and (basis_change < 0 or signal.futures_minus_spot_pct < 0):
            scores.append(25.0)
        elif price > 0 and oi_ratio < 0:
            scores.append(65.0)
        elif price < 0 and oi_ratio < 0:
            scores.append(35.0)
        elif basis_change > 0 and signal.futures_minus_spot_pct > 0:
            scores.append(70.0)
        elif basis_change < 0 and signal.futures_minus_spot_pct < 0:
            scores.append(30.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _daily_basis_structure_component(signals: dict[str, ProductSignal]) -> float:
    scores = []
    for signal in signals.values():
        change = signal.daily_basis_change_bp
        z_value = signal.basis_zscore
        if change is None:
            scores.append(50.0)
        elif change > 0 and (z_value is None or z_value > 0):
            scores.append(90.0 if z_value is not None and z_value > 0.5 else 70.0)
        elif change > 0:
            scores.append(60.0)
        elif change < 0 and (z_value is None or z_value < 0):
            scores.append(10.0 if z_value is not None and z_value < -0.5 else 30.0)
        elif change < 0:
            scores.append(40.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _term_structure_component(snapshot: MarketSnapshot) -> float:
    scores = []
    for terms in snapshot.terms.values():
        clean = [term for term in terms if term.basis_bp is not None]
        if len(clean) < 2:
            continue
        near = clean[0].basis_bp
        far = clean[-1].basis_bp
        if near is None or far is None:
            continue
        slope = far - near
        if near > -20 and slope >= -30:
            scores.append(70.0)
        elif near < -50 and slope < -50:
            scores.append(25.0)
        else:
            scores.append(50.0)
    return _average(scores)


def _tail_component(now: datetime, signals: dict[str, ProductSignal]) -> float:
    if not is_tail_session(now):
        return 50.0
    tracked = [signals[product] for product in RESONANCE_PRODUCTS if product in signals]
    if not tracked:
        return 50.0
    scores = []
    for signal in tracked:
        basis_positive = signal.basis_change_bp is not None and signal.basis_change_bp > 0
        oi_positive = signal.open_interest_change_ratio is not None and signal.open_interest_change_ratio > 0
        price_positive = signal.price_change_5m is not None and signal.price_change_5m > 0
        if basis_positive and oi_positive and price_positive:
            scores.append(100.0)
        elif price_positive and basis_positive:
            scores.append(75.0)
        elif not price_positive and signal.open_interest_change_ratio and signal.open_interest_change_ratio > 0:
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

    net_short_worse = [signal.product for signal in signals.values() if _net_short_expanding(signal)]
    if net_short_worse:
        reasons.append(f"前20净空扩大: {','.join(net_short_worse)}")
        alert_kind = alert_kind or ("im_net_short_expanding" if "IM" in net_short_worse else "net_short_expanding")

    net_short_better = [signal.product for signal in signals.values() if _net_short_contracting(signal)]
    if net_short_better:
        reasons.append(f"前20净空收敛: {','.join(net_short_better)}")

    strong_long = _is_strong_long(signals)
    strong_short = _is_strong_short(signals)
    if strong_long:
        reasons.append("强多组合: 期货领先残差为正 + 基差扩大 + 价仓确认 + IF/IC/IM 共振")
        alert_kind = "strong_long"
    if strong_short:
        reasons.append("强空组合: 期货领先残差为负 + 基差收窄/贴水扩大 + 下跌增仓 + 多品种共振")
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
    clean_count = sum(
        1
        for signal in tracked
        if signal.lead_residual_5m_pct is not None
        and signal.lead_residual_5m_pct > 0
        and signal.basis_zscore is not None
        and signal.basis_zscore > 0
        and signal.price_change_5m is not None
        and signal.price_change_5m > 0
        and signal.open_interest_change_ratio is not None
        and signal.open_interest_change_ratio > 0
        and (signal.net_short_change_top20 is None or signal.net_short_change_top20 <= 0)
        and signal.basis_change_bp is not None
        and signal.basis_change_bp > 0
        and (signal.spot_return_5m_pct is None or signal.spot_return_5m_pct > -0.05)
    )
    return clean_count >= 2 and _spot_breadth_component(signals) >= 50


def _is_strong_short(signals: dict[str, ProductSignal]) -> bool:
    tracked = [signals[product] for product in RESONANCE_PRODUCTS if product in signals]
    if len(tracked) < 2:
        return False
    short_count = sum(
        1
        for signal in tracked
        if signal.lead_residual_5m_pct is not None
        and signal.lead_residual_5m_pct < 0
        and signal.price_change_5m is not None
        and signal.price_change_5m < 0
        and signal.open_interest_change_ratio is not None
        and signal.open_interest_change_ratio > 0
        and (signal.net_short_change_top20 is None or signal.net_short_change_top20 > 0)
        and signal.basis_change_bp is not None
        and signal.basis_change_bp < 0
    )
    return short_count >= 2 and _spot_breadth_component(signals) <= 50


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


def _net_short_expanding(signal: ProductSignal) -> bool:
    if signal.net_short_change_top20_ratio is not None:
        return signal.net_short_change_top20_ratio >= 0.05
    return signal.net_short_change_top20 is not None and signal.net_short_change_top20 >= 1000


def _net_short_contracting(signal: ProductSignal) -> bool:
    if signal.net_short_change_top20_ratio is not None:
        return signal.net_short_change_top20_ratio <= -0.05
    return signal.net_short_change_top20 is not None and signal.net_short_change_top20 <= -1000


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


def _ratio(change: int | float | None, base: int | float | None) -> float | None:
    if change is None or base is None or base == 0:
        return None
    return float(change) / float(base)


def _return_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (float(current) / float(previous) - 1) * 100
