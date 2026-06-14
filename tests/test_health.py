from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from futures_signal.config import Settings
from futures_signal import health
from futures_signal.health import HealthState, _build_handler, start_healthcheck_server


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


def test_health_state_transitions_and_snapshot(tmp_path: Path):
    settings = _settings(tmp_path)
    started_at = datetime(2026, 5, 27, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    state = HealthState(settings=settings, started_at=started_at)

    state.mark_ready()
    assert state.worker_status == "idle"

    state.mark_idle()
    assert state.worker_status == "idle"

    error_at = datetime(2026, 5, 27, 9, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    state.mark_error(error_at)
    snapshot = state.snapshot(datetime(2026, 5, 27, 9, 32, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert snapshot["worker"]["status"] == "error"
    assert snapshot["worker"]["last_error_at"] == error_at.isoformat()

    state.mark_ready()
    assert state.worker_status == "error"

    starting_state = HealthState(settings=settings, started_at=started_at)
    starting_state.mark_idle()
    assert starting_state.worker_status == "starting"


def test_start_healthcheck_server_returns_none_when_disabled(tmp_path: Path):
    settings = _settings(tmp_path)
    settings = Settings(**{**settings.__dict__, "healthcheck_enabled": False})
    state = HealthState(settings=settings, started_at=datetime.now(settings.tz))

    assert start_healthcheck_server(settings, state) is None


def test_start_healthcheck_server_starts_background_thread(tmp_path: Path, monkeypatch):
    settings = Settings(**{**_settings(tmp_path).__dict__, "healthcheck_port": 18081})
    state = HealthState(settings=settings, started_at=datetime.now(settings.tz))
    captured = {}

    class FakeServer:
        def __init__(self, address, handler):
            captured["address"] = address
            captured["handler"] = handler

        def serve_forever(self):
            captured["served"] = True

    class FakeThread:
        def __init__(self, target, name, daemon):
            captured["target"] = target
            captured["name"] = name
            captured["daemon"] = daemon

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(health, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(health.threading, "Thread", FakeThread)

    thread = start_healthcheck_server(settings, state)

    assert isinstance(thread, FakeThread)
    assert captured["address"] == ("127.0.0.1", 18081)
    assert captured["name"] == "healthcheck-server"
    assert captured["daemon"] is True
    assert captured["started"] is True


def test_healthcheck_unknown_path_returns_404_and_head_supports_ok(tmp_path: Path):
    settings = _settings(tmp_path)
    state = HealthState(settings=settings, started_at=datetime.now(settings.tz))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _build_handler(settings, state))
    try:
        from threading import Thread

        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/unknown", method="GET")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request)
        assert exc_info.value.code == 404

        bad_head_request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/unknown", method="HEAD")
        with pytest.raises(urllib.error.HTTPError) as head_exc_info:
            urllib.request.urlopen(bad_head_request)
        assert head_exc_info.value.code == 404

        head_request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/healthz", method="HEAD")
        with urllib.request.urlopen(head_request) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/json; charset=utf-8"
    finally:
        server.shutdown()
        server.server_close()


def test_storage_status_handles_missing_or_unreadable_db(tmp_path: Path, monkeypatch):
    missing = health._storage_status(tmp_path / "missing.db")
    assert missing["db_exists"] is False
    assert missing["db_readable"] is False

    db_path = tmp_path / "market.db"
    db_path.write_text("not-a-sqlite-db", encoding="utf-8")

    def fake_connect(*args, **kwargs):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(health.sqlite3, "connect", fake_connect)
    unreadable = health._storage_status(db_path)

    assert unreadable["db_exists"] is True
    assert unreadable["db_readable"] is False
