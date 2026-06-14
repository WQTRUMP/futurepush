from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _load_dotenv() -> None:
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    enabled = _bool_env("LOAD_DOTENV", app_env not in {"prod", "production"})
    if not enabled:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


@dataclass(frozen=True)
class Settings:
    wecom_webhook_url: str
    timezone_name: str
    sample_interval_seconds: int
    alert_cooldown_seconds: int
    push_every_sample: bool
    run_outside_market_hours: bool
    use_trade_calendar: bool
    trade_calendar_cache_path: Path
    fetch_term_structure: bool
    fetch_term_structure_every_seconds: int
    fetch_position_rank: bool
    position_trend_days: int
    dividend_season_adjust: bool
    basis_history_days: int
    roll_window_days: int
    ai_commentary_enabled: bool
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout_seconds: int
    deepseek_max_tokens: int
    deepseek_temperature: float
    deepseek_thinking_enabled: bool
    deepseek_reasoning_effort: str
    log_level: str
    data_dir: Path
    db_path: Path
    push_policy: str = "daily"
    daily_push_times: str = "09:35,10:30,14:30"
    daily_push_window_seconds: int = 600
    daily_alert_cooldown_seconds: int = 82800
    urgent_alert_cooldown_seconds: int = 3600
    max_quote_age_seconds: int = 180
    max_tick_sync_seconds: int = 60
    position_rank_empty_retry_seconds: int = 900
    allow_custom_ai_base_url: bool = False

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def __post_init__(self) -> None:
        _validate_wecom_webhook_url(self.wecom_webhook_url)
        _validate_ai_base_url(self.deepseek_base_url, self.allow_custom_ai_base_url)

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        return cls(
            wecom_webhook_url=os.getenv("WECOM_WEBHOOK_URL", "").strip(),
            timezone_name=os.getenv("TZ", "Asia/Shanghai").strip() or "Asia/Shanghai",
            sample_interval_seconds=_int_env("SAMPLE_INTERVAL_SECONDS", 60),
            alert_cooldown_seconds=_int_env("ALERT_COOLDOWN_SECONDS", 300),
            push_every_sample=_bool_env("PUSH_EVERY_SAMPLE", False),
            push_policy=os.getenv("PUSH_POLICY", "daily").strip().lower() or "daily",
            daily_push_times=os.getenv("DAILY_PUSH_TIMES", "09:35,10:30,14:30").strip() or "09:35,10:30,14:30",
            daily_push_window_seconds=_int_env("DAILY_PUSH_WINDOW_SECONDS", 600),
            daily_alert_cooldown_seconds=_int_env("DAILY_ALERT_COOLDOWN_SECONDS", 82800),
            urgent_alert_cooldown_seconds=_int_env("URGENT_ALERT_COOLDOWN_SECONDS", 3600),
            max_quote_age_seconds=_int_env("MAX_QUOTE_AGE_SECONDS", 180),
            max_tick_sync_seconds=_int_env("MAX_TICK_SYNC_SECONDS", 60),
            position_rank_empty_retry_seconds=_int_env("POSITION_RANK_EMPTY_RETRY_SECONDS", 900),
            run_outside_market_hours=_bool_env("RUN_OUTSIDE_MARKET_HOURS", False),
            use_trade_calendar=_bool_env("USE_TRADE_CALENDAR", True),
            trade_calendar_cache_path=Path(os.getenv("TRADE_CALENDAR_CACHE_PATH", str(data_dir / "trade_dates.json"))),
            fetch_term_structure=_bool_env("FETCH_TERM_STRUCTURE", True),
            fetch_term_structure_every_seconds=_int_env("FETCH_TERM_STRUCTURE_EVERY_SECONDS", 300),
            fetch_position_rank=_bool_env("FETCH_POSITION_RANK", True),
            position_trend_days=_int_env("POSITION_TREND_DAYS", 5),
            dividend_season_adjust=_bool_env("DIVIDEND_SEASON_ADJUST", True),
            basis_history_days=_int_env("BASIS_HISTORY_DAYS", 20),
            roll_window_days=_int_env("ROLL_WINDOW_DAYS", 7),
            ai_commentary_enabled=_bool_env("AI_COMMENTARY_ENABLED", True),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro",
            deepseek_timeout_seconds=_int_env("DEEPSEEK_TIMEOUT_SECONDS", 20),
            deepseek_max_tokens=_int_env("DEEPSEEK_MAX_TOKENS", 420),
            deepseek_temperature=_float_env("DEEPSEEK_TEMPERATURE", 0.2),
            deepseek_thinking_enabled=_bool_env("DEEPSEEK_THINKING_ENABLED", False),
            deepseek_reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "high").strip() or "high",
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            data_dir=data_dir,
            db_path=Path(os.getenv("DB_PATH", str(data_dir / "market.db"))),
            allow_custom_ai_base_url=_bool_env("ALLOW_CUSTOM_AI_BASE_URL", False),
        )


def _validate_wecom_webhook_url(value: str) -> None:
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError("WECOM_WEBHOOK_URL 必须使用 https")
    if parsed.hostname != "qyapi.weixin.qq.com":
        raise ValueError("WECOM_WEBHOOK_URL 仅允许企业微信官方域名 qyapi.weixin.qq.com")
    if parsed.path != "/cgi-bin/webhook/send":
        raise ValueError("WECOM_WEBHOOK_URL 路径必须为 /cgi-bin/webhook/send")


def _validate_ai_base_url(value: str, allow_custom: bool) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError("DEEPSEEK_BASE_URL 必须使用 https")
    if not allow_custom and parsed.hostname != "api.deepseek.com":
        raise ValueError("DEEPSEEK_BASE_URL 仅允许 api.deepseek.com；如需自定义请开启 ALLOW_CUSTOM_AI_BASE_URL")
