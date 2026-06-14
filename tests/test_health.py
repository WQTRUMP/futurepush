from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from futures_signal.config import Settings
from futures_signal.health import HealthState, _build_handler


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
        deepseek_api_key="super-secret",
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


def test_healthcheck_endpoint_returns_basic_status(tmp_path: Path):
    settings = _settings(tmp_path)
    sqlite3.connect(settings.db_path).close()
    state = HealthState(settings=settings, started_at=datetime(2026, 5, 27, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
    state.mark_sample_ok(datetime(2026, 5, 27, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai")))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _build_handler(settings, state))
    try:
        from threading import Thread

        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/healthz") as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["status"] == "ok"
    assert payload["service"] == "futures-signal"
    assert payload["worker"]["status"] == "ok"
    assert payload["storage"]["db_exists"] is True
    assert payload["storage"]["db_readable"] is True
    assert "super-secret" not in json.dumps(payload, ensure_ascii=False)


def test_health_alias_health_is_supported(tmp_path: Path):
    settings = _settings(tmp_path)
    state = HealthState(settings=settings, started_at=datetime.now(settings.tz))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _build_handler(settings, state))
    try:
        from threading import Thread

        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health") as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
