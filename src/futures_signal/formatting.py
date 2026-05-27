from __future__ import annotations

from .models import MarketAnalysis, ProductSignal


def format_analysis(analysis: MarketAnalysis, ai_commentary: str | None = None) -> str:
    lines = [
        f"A股股指期货信号 {analysis.timestamp:%Y-%m-%d %H:%M:%S}",
        f"总分 {analysis.score}/100 | {analysis.band}",
    ]
    if analysis.previous_score is not None:
        lines.append(f"上次 {analysis.previous_score}/100 | {analysis.previous_band}")

    components = analysis.components
    lines.append(
        "组件 "
        f"基差{components['basis_change']:.0f} "
        f"持仓{components['open_interest']:.0f} "
        f"期现{components['relative_strength']:.0f} "
        f"共振{components['resonance']:.0f} "
        f"尾盘{components['tail']:.0f}"
    )

    lines.append("")
    lines.append("品种 合约 期货%/现货% 期-现bp 状态 Δ5m 分位 持仓Δ 成交Δ 组合")
    for product in ("IF", "IH", "IC", "IM"):
        signal = analysis.signals.get(product)
        if signal is None:
            lines.append(f"{product} 数据缺失")
            continue
        lines.append(_format_signal(signal))

    if analysis.term_summary:
        lines.append("")
        lines.append("期限结构")
        for product in ("IF", "IH", "IC", "IM"):
            summary = analysis.term_summary.get(product)
            if summary:
                lines.append(f"{product} {summary}")

    lines.append("")
    lines.append("触发")
    lines.extend(f"- {reason}" for reason in analysis.reasons)

    if analysis.warnings:
        lines.append("")
        lines.append("数据提示")
        lines.extend(f"- {warning}" for warning in analysis.warnings[:5])

    if ai_commentary:
        lines.append("")
        lines.append("AI点评")
        lines.append(ai_commentary)

    return "\n".join(lines)


def format_once_output(analysis: MarketAnalysis) -> str:
    return format_analysis(analysis)


def _format_signal(signal: ProductSignal) -> str:
    return (
        f"{signal.product} {signal.contract} "
        f"{signal.futures_change_pct:+.2f}%/{signal.spot_change_pct:+.2f}% "
        f"{signal.basis_bp:+.1f} "
        f"{signal.basis_state} "
        f"{_fmt_optional(signal.basis_change_bp, '+.1f')} "
        f"{_fmt_percentile(signal.basis_percentile)} "
        f"{_fmt_delta(signal.open_interest_change)} "
        f"{_fmt_delta(signal.volume_change)} "
        f"{signal.price_oi_signal}"
    )


def _fmt_optional(value: float | None, fmt: str) -> str:
    if value is None:
        return "n/a"
    return format(value, fmt)


def _fmt_delta(value: int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+d}"


def _fmt_percentile(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.0f}%"
