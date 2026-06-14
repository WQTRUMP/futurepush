from pathlib import Path

import json

from futures_signal.ai_commentary import AICommentaryClient
from futures_signal.models import MarketAnalysis, ProductSignal
from futures_signal.config import Settings


def _settings(tmp_path: Path, enabled=True):
    return Settings(
        wecom_webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
        timezone_name="Asia/Shanghai",
        sample_interval_seconds=60,
        alert_cooldown_seconds=300,
        push_every_sample=False,
        run_outside_market_hours=True,
        use_trade_calendar=False,
        trade_calendar_cache_path=tmp_path / "trade_dates.json",
        fetch_term_structure=False,
        fetch_term_structure_every_seconds=300,
        fetch_position_rank=True,
        position_trend_days=5,
        dividend_season_adjust=True,
        basis_history_days=20,
        roll_window_days=7,
        ai_commentary_enabled=enabled,
        deepseek_api_key="",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-pro",
        deepseek_timeout_seconds=20,
        deepseek_max_tokens=420,
        deepseek_temperature=0.2,
        deepseek_thinking_enabled=False,
        deepseek_reasoning_effort="high",
        log_level="INFO",
        data_dir=tmp_path,
        db_path=tmp_path / "market.db",
    )


def test_ai_commentary_disabled_returns_none(tmp_path):
    client = AICommentaryClient(_settings(tmp_path, enabled=False))
    assert client.generate(None) is None


def test_ai_commentary_missing_key_returns_fallback(tmp_path):
    client = AICommentaryClient(_settings(tmp_path, enabled=True))
    assert "未配置 DEEPSEEK_API_KEY" in client.generate(None)


def test_ai_commentary_prompt_requires_sector_tendency(tmp_path, monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True

        def json(self):
            return {"choices": [{"message": {"content": "总判断：中性。\n板块倾向：大金融中性。\n风险：仅供观察。"}}]}

    def fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    settings = _settings(tmp_path, enabled=True)
    settings = settings.__class__(**{**settings.__dict__, "deepseek_api_key": "key"})
    monkeypatch.setattr("futures_signal.ai_commentary.requests.post", fake_post)

    client = AICommentaryClient(settings)
    text = client.generate(_analysis())

    system_prompt = captured["payload"]["messages"][0]["content"]
    user_data = json.loads(captured["payload"]["messages"][1]["content"])
    assert "股票板块" in system_prompt
    assert "前20会员净空变化" in system_prompt
    assert "可能是" not in system_prompt
    assert "疑似" not in system_prompt
    assert "需要观察" not in system_prompt
    assert "sector_mapping" in user_data
    assert "daily_signal_definition" in user_data
    assert "position_rank_definition" in user_data
    assert "可能是" not in json.dumps(user_data["trap_detection"], ensure_ascii=False)
    assert "题材" not in json.dumps(user_data["sector_mapping"], ensure_ascii=False)
    assert "题材躁动" not in json.dumps(user_data["trap_detection"], ensure_ascii=False)
    assert "大金融" in user_data["sector_mapping"]["IH"]
    assert "daily_open_interest_change" in user_data["signals"]["IF"]
    assert "daily_basis_change_bp" in user_data["signals"]["IF"]
    assert "net_short_change_top20" in user_data["signals"]["IF"]
    assert "板块倾向" in text


def _analysis():
    return MarketAnalysis(
        timestamp=__import__("datetime").datetime(2026, 5, 28, 10, 0),
        score=60,
        band="偏多但不强",
        previous_score=50,
        previous_band="中性震荡",
        components={"basis_change": 55, "open_interest": 60, "relative_strength": 65, "resonance": 50, "tail": 50},
        signals={product: _signal(product) for product in ("IF", "IH", "IC", "IM")},
        reasons=["测试"],
        warnings=[],
        alert_kind="sample",
    )


def _signal(product):
    return ProductSignal(
        product=product,
        product_name=product,
        contract=f"{product}2606",
        previous_contract=None,
        futures_price=4800,
        futures_change_pct=0.3,
        spot_price=4780,
        spot_change_pct=0.1,
        basis=20,
        basis_bp=41.84,
        basis_state="升水",
        basis_change_bp=3.0,
        basis_change_label="贴水收窄/升水扩大",
        basis_percentile=0.6,
        basis_zscore=None,
        basis_history_count=20,
        futures_minus_spot_pct=0.2,
        lead_beta=1.0,
        futures_return_5m_pct=0.3,
        spot_return_5m_pct=0.1,
        lead_residual_5m_pct=0.2,
        volume=10000,
        volume_change=500,
        volume_change_ratio=0.05,
        open_interest=20000,
        open_interest_change=300,
        price_change_5m=None,
        price_oi_signal="多头主动开仓",
        main_contract_changed=False,
        daily_price_change=18,
        daily_open_interest_change=1200,
        daily_basis_change_bp=5,
    )
