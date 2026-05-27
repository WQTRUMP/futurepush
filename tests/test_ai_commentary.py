from pathlib import Path

from futures_signal.ai_commentary import AICommentaryClient
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
        dividend_season_adjust=True,
        basis_history_days=20,
        roll_window_days=7,
        ai_commentary_enabled=enabled,
        deepseek_api_key="",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-v4-pro",
        deepseek_timeout_seconds=20,
        deepseek_max_tokens=260,
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
