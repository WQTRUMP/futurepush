import pytest

from futures_signal.config import Settings


def test_settings_rejects_non_wecom_host(tmp_path):
    with pytest.raises(ValueError, match="WECOM_WEBHOOK_URL"):
        Settings(
            wecom_webhook_url="https://example.com/webhook",
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
            ai_commentary_enabled=False,
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


def test_settings_rejects_custom_ai_base_url_without_override(tmp_path):
    with pytest.raises(ValueError, match="DEEPSEEK_BASE_URL"):
        Settings(
            wecom_webhook_url="",
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
            ai_commentary_enabled=True,
            deepseek_api_key="key",
            deepseek_base_url="https://gateway.example.com",
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
