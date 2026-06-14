from datetime import datetime
from zoneinfo import ZoneInfo

from futures_signal.formatting import format_analysis
from futures_signal.models import MarketAnalysis, PositionTrendSignal, ProductSignal


def _signal(
    product: str,
    futures_minus_spot_pct: float = 0.2,
    basis_change_bp: float = 2.5,
    open_interest_change: int = 120,
    price_change_5m: float = 10,
    daily_open_interest_change: int | None = 500,
    daily_price_change: float | None = 20,
    daily_basis_change_bp: float | None = 4,
    net_short_change_top20: int | None = None,
    citic_net_short_change: int | None = None,
) -> ProductSignal:
    return ProductSignal(
        product=product,
        product_name=product,
        contract=f"{product}2606",
        previous_contract=None,
        futures_price=4800,
        futures_change_pct=0.12,
        spot_price=4850,
        spot_change_pct=-0.08,
        basis=-50,
        basis_bp=-103.1,
        basis_state="贴水",
        basis_change_bp=basis_change_bp,
        basis_change_label="贴水收窄/升水扩大",
        basis_percentile=0.28,
        basis_zscore=None,
        basis_history_count=12,
        futures_minus_spot_pct=futures_minus_spot_pct,
        lead_beta=1.0,
        futures_return_5m_pct=0.3 if price_change_5m >= 0 else -0.3,
        spot_return_5m_pct=0.1,
        lead_residual_5m_pct=0.2 if futures_minus_spot_pct >= 0 else -0.2,
        volume=10000,
        volume_change=120,
        volume_change_ratio=0.012,
        open_interest=20000,
        open_interest_change=open_interest_change,
        price_change_5m=price_change_5m,
        price_oi_signal="变化不明显",
        main_contract_changed=False,
        daily_price_change=daily_price_change,
        daily_open_interest_change=daily_open_interest_change,
        daily_basis_change_bp=daily_basis_change_bp,
        net_short_change_top20=net_short_change_top20,
        citic_net_short_change=citic_net_short_change,
    )


def test_format_analysis_uses_conclusion_first_mobile_format():
    analysis = MarketAnalysis(
        timestamp=datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        score=46,
        band="中性震荡",
        previous_score=45,
        previous_band="中性震荡",
        components={
            "basis_change": 50,
            "open_interest": 50,
            "relative_strength": 32,
            "resonance": 50,
            "tail": 50,
        },
        signals={
            "IF": _signal("IF", futures_minus_spot_pct=0.3, basis_change_bp=3, open_interest_change=200, price_change_5m=12),
            "IH": _signal("IH", futures_minus_spot_pct=0.1, basis_change_bp=2, open_interest_change=100, price_change_5m=5),
            "IC": _signal("IC", futures_minus_spot_pct=-0.2, basis_change_bp=-2, open_interest_change=180, price_change_5m=-8, daily_price_change=-15, daily_basis_change_bp=-4),
            "IM": _signal("IM", futures_minus_spot_pct=-0.3, basis_change_bp=-3, open_interest_change=220, price_change_5m=-10, daily_price_change=-20, daily_basis_change_bp=-5, net_short_change_top20=1479, citic_net_short_change=749),
        },
        reasons=["测试触发"],
        warnings=[],
        alert_kind="sample",
        term_summary={"IF": "IF2606:-103.1bp(贴水) IF2607:-180.0bp(贴水)"},
    )

    text = format_analysis(analysis, ai_commentary="AI正常")

    assert text.startswith("🟧 黄灯偏橙：A股震荡")
    assert "\n----\n结论：" in text
    assert "\n----\n依据：" in text
    assert "结论：少碰中证1000/高弹性小票，优先看沪深300/上证50" in text
    assert "操作：尾盘不追高；只低吸缩量回踩、承接强的票" in text
    assert "依据：IF,IH期货领先偏多；IC,IM期货领先偏空；IF/IH强于IC/IM；IM净空扩大；中信IM偏空" in text
    assert "IF/IH强于IC/IM" in text
    assert "小盘题材" not in text
    assert "题材强于权重" not in text
    assert "增仓信号已降级为空方压力" not in text
    assert "判断依据" not in text
    assert "风险结论" not in text
    assert "可能是" not in text
    assert "疑似" not in text
    assert "品种明细" not in text
    assert "期限结构" not in text
    assert "IF2606" not in text
    assert "期货 +0.12% | 现货 -0.08%" not in text
    assert "AI正常" in text


def test_format_analysis_adds_position_trend_only_when_requested():
    analysis = MarketAnalysis(
        timestamp=datetime(2026, 5, 28, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        score=46,
        band="中性震荡",
        previous_score=None,
        previous_band=None,
        components={},
        signals={"IM": _signal("IM", futures_minus_spot_pct=-0.3, basis_change_bp=-3, net_short_change_top20=1200)},
        reasons=[],
        warnings=[],
        alert_kind="sample",
        position_trends={
            "IM": PositionTrendSignal("IM", days=5, net_short_change_sum=4200, latest_net_short_change=1200),
            "IF": PositionTrendSignal("IF", days=5, net_short_change_sum=-1800, latest_net_short_change=-200),
        },
    )

    assert "持仓趋势" not in format_analysis(analysis)
    text = format_analysis(analysis, include_position_trend=True)

    assert "\n----\n持仓趋势：" in text
    assert "持仓趋势：IM净空累增，IF净空收敛" in text


def test_format_analysis_keeps_multiple_ai_lines():
    analysis = MarketAnalysis(
        timestamp=datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        score=55,
        band="中性震荡",
        previous_score=None,
        previous_band=None,
        components={},
        signals={},
        reasons=["测试"],
        warnings=[],
        alert_kind="sample",
    )

    text = format_analysis(
        analysis,
        ai_commentary="走势：震荡偏强\n节奏：等回踩\n板块：权重优先\n风险：冲高回落",
    )

    assert "AI：走势：震荡偏强\n节奏：等回踩\n板块：权重优先\n风险：冲高回落" in text
