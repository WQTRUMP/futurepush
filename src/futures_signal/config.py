from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


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


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    timezone_name: str
    sample_interval_seconds: int
    alert_cooldown_seconds: int
    push_every_sample: bool
    run_outside_market_hours: bool
    use_trade_calendar: bool
    trade_calendar_cache_path: Path
    fetch_term_structure: bool
    fetch_term_structure_every_seconds: int
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

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            timezone_name=os.getenv("TZ", "Asia/Shanghai").strip() or "Asia/Shanghai",
            sample_interval_seconds=_int_env("SAMPLE_INTERVAL_SECONDS", 60),
            alert_cooldown_seconds=_int_env("ALERT_COOLDOWN_SECONDS", 300),
            push_every_sample=_bool_env("PUSH_EVERY_SAMPLE", False),
            run_outside_market_hours=_bool_env("RUN_OUTSIDE_MARKET_HOURS", False),
            use_trade_calendar=_bool_env("USE_TRADE_CALENDAR", True),
            trade_calendar_cache_path=Path(os.getenv("TRADE_CALENDAR_CACHE_PATH", str(data_dir / "trade_dates.json"))),
            fetch_term_structure=_bool_env("FETCH_TERM_STRUCTURE", True),
            fetch_term_structure_every_seconds=_int_env("FETCH_TERM_STRUCTURE_EVERY_SECONDS", 300),
            dividend_season_adjust=_bool_env("DIVIDEND_SEASON_ADJUST", True),
            basis_history_days=_int_env("BASIS_HISTORY_DAYS", 20),
            roll_window_days=_int_env("ROLL_WINDOW_DAYS", 7),
            ai_commentary_enabled=_bool_env("AI_COMMENTARY_ENABLED", True),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro",
            deepseek_timeout_seconds=_int_env("DEEPSEEK_TIMEOUT_SECONDS", 20),
            deepseek_max_tokens=_int_env("DEEPSEEK_MAX_TOKENS", 260),
            deepseek_temperature=_float_env("DEEPSEEK_TEMPERATURE", 0.2),
            deepseek_thinking_enabled=_bool_env("DEEPSEEK_THINKING_ENABLED", False),
            deepseek_reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "high").strip() or "high",
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            data_dir=data_dir,
            db_path=Path(os.getenv("DB_PATH", str(data_dir / "market.db"))),
        )
