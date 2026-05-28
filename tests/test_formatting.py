from datetime import datetime
from zoneinfo import ZoneInfo

from futures_signal.formatting import format_analysis
from futures_signal.models import MarketAnalysis, ProductSignal


def _signal(product: str) -> ProductSignal:
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
        basis_change_bp=2.5,
        basis_change_label="贴水收窄/升水扩大",
        basis_percentile=0.28,
        basis_zscore=None,
        basis_history_count=12,
        futures_minus_spot_pct=-1.03,
        volume=10000,
        volume_change=120,
        open_interest=20000,
        open_interest_change=-80,
        price_change_5m=None,
        price_oi_signal="变化不明显",
        main_contract_changed=False,
    )


def test_format_analysis_uses_mobile_friendly_blocks():
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
        signals={product: _signal(product) for product in ("IF", "IH", "IC", "IM")},
        reasons=["测试触发"],
        warnings=[],
        alert_kind="sample",
        term_summary={"IF": "IF2606:-103.1bp(贴水) IF2607:-180.0bp(贴水)"},
    )

    text = format_analysis(analysis, ai_commentary="AI正常")

    assert "品种 合约 期货%/现货%" not in text
    assert "品种明细\nIF IF2606\n  期货 +0.12% | 现货 -0.08%" in text
    assert "期限结构\nIF\n  IF2606:-103.1bp(贴水)" in text
    assert "AI点评\nAI正常" in text
