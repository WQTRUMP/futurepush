import logging
import os
from pathlib import Path

from futures_signal.cli import _SensitiveDataFilter, configure_logging
from futures_signal.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
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


def test_sensitive_data_filter_redacts_query_secrets():
    redacted = _SensitiveDataFilter()._redact(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret&foo=bar"
    )

    assert "key=***" in redacted
    assert "foo=bar" in redacted
    assert "secret" not in redacted


def test_configure_logging_creates_restricted_log_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    for handler in original_handlers:
        root.removeHandler(handler)

    try:
        configure_logging(_settings(tmp_path))
        log_path = tmp_path / "logs" / "futures_signal.log"
        assert log_path.exists()
        assert oct(os.stat(log_path.parent).st_mode & 0o777) == "0o700"
        assert oct(os.stat(log_path).st_mode & 0o777) == "0o600"
    finally:
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)
        root.setLevel(original_level)
        for handler in original_handlers:
            root.addHandler(handler)
