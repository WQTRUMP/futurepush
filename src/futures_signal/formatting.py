from __future__ import annotations

from .models import MarketAnalysis, ProductSignal

SECTION_SEPARATOR = "----"


def format_analysis(
    analysis: MarketAnalysis,
    ai_commentary: str | None = None,
    include_position_trend: bool = False,
) -> str:
    lamp = _lamp_label(analysis)
    focus = _focus_line(analysis)
    action = _action_line(analysis)
    evidence = _compact_evidence(analysis)
    sections = [
        [
            f"{_lamp_icon(lamp)} {lamp}：A股{_direction_label(analysis.score)}",
            f"周期：{_horizon_label(analysis.timestamp)} | 置信度：{_confidence_label(analysis.score)}",
        ],
        [
            focus,
            action,
        ],
    ]
    if evidence:
        sections.append([f"依据：{evidence}"])

    if include_position_trend:
        trend = _position_trend_line(analysis)
        if trend:
            sections.append([trend])

    if analysis.warnings:
        sections.append([f"数据：{analysis.warnings[0]}"])

    if ai_commentary:
        compact_ai = _compact_ai(ai_commentary)
        if compact_ai:
            sections.append([compact_ai])

    sections.append([f"{analysis.timestamp:%m-%d %H:%M}"])

    return f"\n{SECTION_SEPARATOR}\n".join("\n".join(section) for section in sections if section)


def format_once_output(analysis: MarketAnalysis) -> str:
    return format_analysis(analysis)


def _direction_label(score: int) -> str:
    if score >= 80:
        return "强偏多"
    if score >= 60:
        return "偏多"
    if score >= 40:
        return "震荡"
    if score >= 20:
        return "偏空"
    return "强偏空"


def _confidence_label(score: int) -> str:
    if score >= 80 or score <= 19:
        return "高确定性"
    if score >= 60 or score <= 39:
        return "中等确定性"
    return "低确定性"


def _trading_hint(score: int) -> str:
    if score >= 80:
        return "T+1 可偏进攻，优先等回踩确认"
    if score >= 60:
        return "T+1 可轻仓偏多，不追高"
    if score >= 40:
        return "T+1 以观察和仓位控制为主"
    if score >= 20:
        return "T+1 偏防守，反弹不追"
    return "T+1 优先降风险，控制回撤"


def _horizon_label(now) -> str:
    current = now.time()
    if (current.hour == 14 and current.minute >= 30) or current.hour >= 15:
        return "次日开盘/次日全天"
    if current.hour < 10:
        return "盘中30分钟"
    return "盘中至收盘"


def _lamp_label(analysis: MarketAnalysis) -> str:
    signals = analysis.signals
    im = signals.get("IM")
    red = (
        analysis.score <= 19
        or sum(1 for signal in signals.values() if _net_short_expanding(signal) and _is_suspicious_long(signal)) >= 2
    )
    orange = (
        analysis.score <= 39
        or (im is not None and _net_short_expanding(im))
        or _is_weight_support_small_cap_weak(signals)
    )
    green = (
        analysis.score >= 70
        and not (im is not None and _net_short_expanding(im))
        and not _is_weight_support_small_cap_weak(signals)
    )
    if red:
        return "红灯"
    if orange and analysis.score >= 40:
        return "黄灯偏橙"
    if orange:
        return "橙灯"
    if green:
        return "绿灯"
    return "黄灯"


def _lamp_icon(lamp: str) -> str:
    if lamp == "绿灯":
        return "🟢"
    if lamp == "黄灯":
        return "🟡"
    if lamp == "黄灯偏橙":
        return "🟧"
    if lamp == "橙灯":
        return "🟠"
    if lamp == "红灯":
        return "🔴"
    return "⚪"


def _focus_line(analysis: MarketAnalysis) -> str:
    signals = analysis.signals
    im = signals.get("IM")
    if im and (_net_short_expanding(im) or _citic_net_short_expanding(im) or _style_decision(im) == "bearish"):
        return "结论：少碰中证1000/高弹性小票，优先看沪深300/上证50"
    if _is_weight_support_small_cap_weak(signals):
        return "结论：小票风险高于权重，少开中证1000方向新仓"
    if _is_small_cap_hot_weight_weak(signals):
        return "结论：中小盘短线更活跃，但指数不稳，盈利票别恋战"
    if analysis.score >= 70:
        return "结论：可以做多，优先买回踩不破位的强势股"
    if analysis.score <= 39:
        return "结论：先防守，少开新仓，持仓冲高先减"
    return "结论：方向不够明确，轻仓做，别追高"


def _action_line(analysis: MarketAnalysis) -> str:
    signals = analysis.signals
    im = signals.get("IM")
    if im and (_net_short_expanding(im) or _style_decision(im) == "bearish"):
        return "操作：尾盘不追高；只低吸缩量回踩、承接强的票"
    if analysis.score >= 70:
        return "操作：仓位可加一点，买点放在回踩确认后"
    if analysis.score <= 39:
        return "操作：反弹先卖弱票，避免尾盘新开仓"
    return "操作：只做低吸，不追涨；弱票先处理"


def _compact_evidence(analysis: MarketAnalysis) -> str:
    items: list[str] = []
    signals = analysis.signals
    net_short_up = [signal.product for signal in signals.values() if _net_short_expanding(signal)]
    net_short_down = [signal.product for signal in signals.values() if _net_short_contracting(signal)]
    citic_up = [signal.product for signal in signals.values() if _citic_net_short_expanding(signal)]
    lead_up = [signal.product for signal in signals.values() if _lead_residual_positive(signal)]
    lead_down = [signal.product for signal in signals.values() if _lead_residual_negative(signal)]
    if lead_up:
        items.append(f"{','.join(lead_up)}期货领先偏多")
    if lead_down:
        items.append(f"{','.join(lead_down)}期货领先偏空")
    if _is_weight_support_small_cap_weak(signals):
        items.append("IF/IH强于IC/IM")
    elif _is_small_cap_hot_weight_weak(signals):
        items.append("IC/IM强于IF/IH")
    if net_short_up:
        items.append(f"{','.join(net_short_up)}净空扩大")
    if net_short_down:
        items.append(f"{','.join(net_short_down)}净空收敛")
    if citic_up:
        items.append(f"中信{','.join(citic_up)}偏空")
    traps = _trap_lines(analysis)
    if traps:
        items.append(_shorten_trap_line(traps[0]))
    if not items:
        items.extend(_clean_reason(reason) for reason in analysis.reasons[:2])
    return "；".join(items[:5])


def _shorten_trap_line(line: str) -> str:
    if "增仓伴随期现或基差走弱" in line:
        products = line.split(" ", 1)[0]
        return f"{products}期货增仓走弱"
    if "上涨减仓" in line:
        products = line.split(" ", 1)[0]
        return f"{products}上涨减仓，不追"
    return line.replace("，不作为看多依据", "")


def _position_trend_line(analysis: MarketAnalysis) -> str:
    trends = analysis.position_trends
    if not trends:
        return ""
    pressure = [product for product, trend in trends.items() if trend.net_short_change_sum >= 1000]
    easing = [product for product, trend in trends.items() if trend.net_short_change_sum <= -1000]
    if pressure and easing:
        return f"持仓趋势：{','.join(pressure)}净空累增，{','.join(easing)}净空收敛"
    if pressure:
        return f"持仓趋势：{','.join(pressure)}近{trends[pressure[0]].days}日净空累增，T+1偏防守"
    if easing:
        return f"持仓趋势：{','.join(easing)}近{trends[easing[0]].days}日净空收敛，T+1压力缓和"
    return "持仓趋势：近几日净空变化不极端，T+1看盘中确认"


def _compact_ai(ai_commentary: str, max_lines: int = 5, max_chars: int = 360) -> str:
    lines = [line.strip() for line in ai_commentary.splitlines() if line.strip()]
    if not lines:
        return ""
    lines = lines[:max_lines]
    if not lines[0].startswith("AI"):
        lines[0] = f"AI：{lines[0]}"
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


def _clean_reason(reason: str) -> str:
    return reason.replace("评分档位变化:", "评分状态变化:")


def _market_structure_lines(analysis: MarketAnalysis) -> list[str]:
    lines: list[str] = []
    for product in ("IF", "IH", "IC", "IM"):
        signal = analysis.signals.get(product)
        if signal is None:
            continue
        label = _style_label(product)
        bias = _signal_bias(signal)
        if bias:
            lines.append(f"{product}（{label}）{bias}")
    if not lines:
        lines.extend(_clean_reason(reason) for reason in analysis.reasons[:3])
    return lines[:4]


def _decision_lines(analysis: MarketAnalysis) -> list[str]:
    lines: list[str] = []
    bullish = []
    bearish = []
    neutral = []
    for product in ("IF", "IH", "IC", "IM"):
        signal = analysis.signals.get(product)
        if signal is None:
            continue
        label = f"{product}（{_style_label(product)}）"
        decision = _style_decision(signal)
        if decision == "bullish":
            bullish.append(label)
        elif decision == "bearish":
            bearish.append(label)
        else:
            neutral.append(label)

    if bullish:
        lines.append(f"{'、'.join(bullish)}：资金结构偏多，作为正向支撑")
    if bearish:
        lines.append(f"{'、'.join(bearish)}：资金结构偏空，压制风险偏好")
    if neutral:
        lines.append(f"{'、'.join(neutral)}：资金结构不清晰，暂不作为方向依据")
    if not lines:
        lines.extend(_clean_reason(reason) for reason in analysis.reasons[:3])
    return lines[:3]


def _style_decision(signal: ProductSignal) -> str:
    daily_basis_up = signal.daily_basis_change_bp is not None and signal.daily_basis_change_bp > 0
    daily_basis_down = signal.daily_basis_change_bp is not None and signal.daily_basis_change_bp < 0
    daily_oi_up = signal.daily_open_interest_change is not None and signal.daily_open_interest_change > 0
    daily_price_up = signal.daily_price_change is not None and signal.daily_price_change > 0
    daily_price_down = signal.daily_price_change is not None and signal.daily_price_change < 0
    basis_up = signal.basis_change_bp is not None and signal.basis_change_bp > 0
    basis_down = signal.basis_change_bp is not None and signal.basis_change_bp < 0

    if daily_price_up and daily_oi_up and daily_basis_up and signal.futures_minus_spot_pct > 0:
        return "bullish"
    if daily_oi_up and (daily_basis_down or signal.futures_minus_spot_pct < 0):
        return "bearish"
    if daily_price_down and daily_oi_up and daily_basis_down:
        return "bearish"
    if signal.open_interest_change is not None and signal.open_interest_change > 0 and basis_up and signal.futures_minus_spot_pct > 0:
        return "bullish"
    if signal.open_interest_change is not None and signal.open_interest_change > 0 and (basis_down or signal.futures_minus_spot_pct < 0):
        return "bearish"
    return "neutral"


def _signal_bias(signal: ProductSignal) -> str | None:
    daily = _daily_signal_bias(signal)
    if daily:
        return daily

    basis_up = signal.basis_change_bp is not None and signal.basis_change_bp > 0
    basis_down = signal.basis_change_bp is not None and signal.basis_change_bp < 0
    oi_up = signal.open_interest_change is not None and signal.open_interest_change > 0
    oi_down = signal.open_interest_change is not None and signal.open_interest_change < 0
    price_up = signal.price_change_5m is not None and signal.price_change_5m > 0
    price_down = signal.price_change_5m is not None and signal.price_change_5m < 0
    futures_stronger = signal.futures_minus_spot_pct > 0
    futures_weaker = signal.futures_minus_spot_pct < 0

    if price_up and oi_up and basis_up and futures_stronger:
        return "多单主动加仓：期货强于现货，基差改善，增仓配合"
    if price_down and oi_up and basis_down and futures_weaker:
        return "空单主动加仓：期货弱于现货，基差走坏，增仓压制"
    if price_up and oi_down:
        return "反弹但减仓：更像空头回补，不算扎实做多"
    if price_down and oi_down:
        return "下跌但减仓：按多头撤退或止损处理，杀跌持续性降低"
    if oi_up and basis_up and futures_stronger:
        return "偏多增仓：期货预期改善，按轻仓偏多处理"
    if oi_up and (basis_down or futures_weaker):
        return "增仓不等于看多：更像套保/对冲或空头加仓"
    if basis_up and futures_stronger:
        return "预期改善：期货强于现货，贴水收窄或升水扩大"
    if basis_down and futures_weaker:
        return "预期走弱：期货弱于现货，贴水扩大或升水收窄"
    return None


def _daily_signal_bias(signal: ProductSignal) -> str | None:
    daily_oi_up = signal.daily_open_interest_change is not None and signal.daily_open_interest_change > 0
    daily_oi_down = signal.daily_open_interest_change is not None and signal.daily_open_interest_change < 0
    daily_price_up = signal.daily_price_change is not None and signal.daily_price_change > 0
    daily_price_down = signal.daily_price_change is not None and signal.daily_price_change < 0
    daily_basis_up = signal.daily_basis_change_bp is not None and signal.daily_basis_change_bp > 0
    daily_basis_down = signal.daily_basis_change_bp is not None and signal.daily_basis_change_bp < 0
    futures_stronger = signal.futures_minus_spot_pct > 0
    futures_weaker = signal.futures_minus_spot_pct < 0

    if daily_price_up and daily_oi_up and daily_basis_up and futures_stronger:
        return "日线多单主动加仓：价格、持仓、基差和期现强弱同步偏多"
    if daily_price_down and daily_oi_up and daily_basis_down and futures_weaker:
        return "日线空单主动加仓：价格走弱、持仓增加、基差走坏"
    if daily_oi_up and (daily_basis_down or futures_weaker):
        return "日线增仓不等于看多：基差或期现强弱不配合，按套保/对冲压力处理"
    if daily_price_up and daily_oi_down:
        return "日线上涨但减仓：更像空头回补，趋势确认不足"
    if daily_price_down and daily_oi_down:
        return "日线下跌但减仓：更像多头撤退，延续性看后续承接"
    return None


def _trap_lines(analysis: MarketAnalysis) -> list[str]:
    signals = analysis.signals
    lines: list[str] = []
    suspicious_long = [
        signal.product
        for signal in signals.values()
        if _is_suspicious_long(signal)
    ]
    if suspicious_long:
        lines.append(f"{','.join(suspicious_long)} 增仓伴随期现或基差走弱，按空方/对冲压力处理，不作为看多依据")

    unconfirmed_intraday = [
        signal.product
        for signal in signals.values()
        if signal.open_interest_change is not None
        and signal.open_interest_change > 0
        and (signal.daily_open_interest_change is None or signal.daily_open_interest_change <= 0)
    ]
    if unconfirmed_intraday:
        lines.append(f"{','.join(unconfirmed_intraday)} 短线增仓未获日线确认，方向权重已降低")

    rebound_cover = [
        signal.product
        for signal in signals.values()
        if signal.price_change_5m is not None
        and signal.price_change_5m > 0
        and signal.open_interest_change is not None
        and signal.open_interest_change < 0
    ]
    if rebound_cover:
        lines.append(f"{','.join(rebound_cover)} 上涨减仓已降级，不能作为主动做多依据")

    if _is_weight_support_small_cap_weak(signals):
        lines.append("权重强、小票弱，结论已降级为结构性行情")

    if _is_small_cap_hot_weight_weak(signals):
        lines.append("中证1000/中盘成长强、权重弱，指数级别持续性已打折")

    if not lines and _has_aligned_confirmation(signals):
        lines.append("期现强弱、基差、持仓方向一致，结论可信度提高")

    return lines[:3]


def _is_suspicious_long(signal: ProductSignal) -> bool:
    intraday_bad = (
        signal.open_interest_change is not None
        and signal.open_interest_change > 0
        and (signal.futures_minus_spot_pct < 0 or (signal.basis_change_bp is not None and signal.basis_change_bp < 0))
    )
    daily_bad = (
        signal.daily_open_interest_change is not None
        and signal.daily_open_interest_change > 0
        and (
            signal.futures_minus_spot_pct < 0
            or (signal.daily_basis_change_bp is not None and signal.daily_basis_change_bp < 0)
        )
    )
    return intraday_bad or daily_bad


def _lead_residual_positive(signal: ProductSignal) -> bool:
    return signal.lead_residual_5m_pct is not None and signal.lead_residual_5m_pct > 0


def _lead_residual_negative(signal: ProductSignal) -> bool:
    return signal.lead_residual_5m_pct is not None and signal.lead_residual_5m_pct < 0


def _net_short_expanding(signal: ProductSignal) -> bool:
    if signal.net_short_change_top20_ratio is not None:
        return signal.net_short_change_top20_ratio >= 0.05
    return signal.net_short_change_top20 is not None and signal.net_short_change_top20 >= 1000


def _net_short_contracting(signal: ProductSignal) -> bool:
    if signal.net_short_change_top20_ratio is not None:
        return signal.net_short_change_top20_ratio <= -0.05
    return signal.net_short_change_top20 is not None and signal.net_short_change_top20 <= -1000


def _citic_net_short_expanding(signal: ProductSignal) -> bool:
    if signal.citic_net_short_change_ratio is not None:
        return signal.citic_net_short_change_ratio >= 0.025
    return signal.citic_net_short_change is not None and signal.citic_net_short_change >= 500


def _has_aligned_confirmation(signals: dict[str, ProductSignal]) -> bool:
    aligned = 0
    for signal in signals.values():
        basis_up = signal.basis_change_bp is not None and signal.basis_change_bp > 0
        basis_down = signal.basis_change_bp is not None and signal.basis_change_bp < 0
        oi_up = signal.open_interest_change is not None and signal.open_interest_change > 0
        if signal.futures_minus_spot_pct > 0 and basis_up and oi_up:
            aligned += 1
        elif signal.futures_minus_spot_pct < 0 and basis_down and oi_up:
            aligned += 1
    return aligned >= 2


def _is_weight_support_small_cap_weak(signals: dict[str, ProductSignal]) -> bool:
    weight = [signals[p].futures_minus_spot_pct for p in ("IF", "IH") if p in signals]
    small = [signals[p].futures_minus_spot_pct for p in ("IC", "IM") if p in signals]
    return bool(weight and small) and sum(v > 0 for v in weight) >= 1 and sum(v < 0 for v in small) >= 1


def _is_small_cap_hot_weight_weak(signals: dict[str, ProductSignal]) -> bool:
    weight = [signals[p].futures_minus_spot_pct for p in ("IF", "IH") if p in signals]
    small = [signals[p].futures_minus_spot_pct for p in ("IC", "IM") if p in signals]
    return bool(weight and small) and sum(v < 0 for v in weight) >= 1 and sum(v > 0 for v in small) >= 1


def _style_label(product: str) -> str:
    return {
        "IF": "核心权重",
        "IH": "大金融/红利",
        "IC": "中盘成长",
        "IM": "中证1000/小盘成长",
    }.get(product, product)
