import builtins
import sys
import types

import pytest

from futures_signal import config
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


def test_settings_rejects_non_https_wecom_url(tmp_path):
    with pytest.raises(ValueError, match="https"):
        Settings(
            wecom_webhook_url="http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
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


def test_settings_rejects_non_https_ai_base_url(tmp_path):
    with pytest.raises(ValueError, match="https"):
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
            deepseek_base_url="http://api.deepseek.com",
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


def test_settings_rejects_wrong_wecom_path(tmp_path):
    with pytest.raises(ValueError, match="路径必须为 /cgi-bin/webhook/send"):
        Settings(
            wecom_webhook_url="https://qyapi.weixin.qq.com/webhook/send?key=test",
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


def test_load_dotenv_skips_in_production_by_default(monkeypatch):
    called = {"count": 0}

    def fake_load_dotenv():
        called["count"] += 1

    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=fake_load_dotenv))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LOAD_DOTENV", raising=False)

    config._load_dotenv()

    assert called["count"] == 0


def test_settings_from_env_allows_custom_ai_base_url_when_enabled(tmp_path, monkeypatch):
    called = {"count": 0}

    def fake_load_dotenv():
        called["count"] += 1

    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=fake_load_dotenv))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOAD_DOTENV", "true")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://gateway.example.com")
    monkeypatch.setenv("ALLOW_CUSTOM_AI_BASE_URL", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "market.db"))

    settings = Settings.from_env()

    assert called["count"] == 1
    assert settings.allow_custom_ai_base_url is True
    assert settings.deepseek_base_url == "https://gateway.example.com"


def test_load_dotenv_ignores_missing_dependency(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dotenv":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LOAD_DOTENV", "true")

    config._load_dotenv()
