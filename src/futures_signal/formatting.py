from __future__ import annotations

from .models import MarketAnalysis, ProductSignal


def format_analysis(analysis: MarketAnalysis, ai_commentary: str | None = None) -> str:
    lines = [
        "A股股指期货信号",
        f"时间 {analysis.timestamp:%Y-%m-%d %H:%M:%S}",
        f"评分 {analysis.score}/100",
        f"状态 {analysis.band}",
    ]
    if analysis.previous_score is not None:
        lines.append(f"上次 {analysis.previous_score}/100 | {analysis.previous_band}")

    components = analysis.components
    lines.append("")
    lines.append("分项")
    lines.append(
        f"基差{components['basis_change']:.0f} "
        f"持仓{components['open_interest']:.0f} "
        f"期现{components['relative_strength']:.0f} "
        f"共振{components['resonance']:.0f} "
        f"尾盘{components['tail']:.0f}"
    )

    lines.append("")
    lines.append("品种明细")
    for product in ("IF", "IH", "IC", "IM"):
        signal = analysis.signals.get(product)
        if signal is None:
            lines.append(f"{product} 数据缺失")
            continue
        lines.extend(_format_signal(signal))

    if analysis.term_summary:
        lines.append("")
        lines.append("期限结构")
        for product in ("IF", "IH", "IC", "IM"):
            summary = analysis.term_summary.get(product)
            if summary:
                lines.append(product)
                lines.extend(f"  {item}" for item in summary.split())

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


def _format_signal(signal: ProductSignal) -> list[str]:
    return [
        f"{signal.product} {signal.contract}",
        f"  期货 {signal.futures_change_pct:+.2f}% | 现货 {signal.spot_change_pct:+.2f}%",
        (
            f"  基差 {signal.basis_bp:+.1f}bp {signal.basis_state} | "
            f"Δ5m {_fmt_optional(signal.basis_change_bp, '+.1f')} | "
            f"分位 {_fmt_percentile(signal.basis_percentile)}"
        ),
        f"  持仓Δ {_fmt_delta(signal.open_interest_change)} | 成交Δ {_fmt_delta(signal.volume_change)}",
        f"  组合 {signal.price_oi_signal}",
    ]


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
