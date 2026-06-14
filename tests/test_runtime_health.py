from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_run_command_serves_healthcheck_while_process_is_alive(tmp_path: Path):
    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "production",
            "LOAD_DOTENV": "false",
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "DATA_DIR": str(tmp_path),
            "DB_PATH": str(tmp_path / "market.db"),
            "USE_TRADE_CALENDAR": "false",
            "RUN_OUTSIDE_MARKET_HOURS": "false",
            "SAMPLE_INTERVAL_SECONDS": "3600",
            "AI_COMMENTARY_ENABLED": "false",
            "HEALTHCHECK_HOST": "127.0.0.1",
            "HEALTHCHECK_PORT": str(port),
            "HEALTHCHECK_PATH": "/healthz",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "futures_signal", "run"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        payload = _wait_for_healthcheck(port)
        alias_status = _read_status(f"http://127.0.0.1:{port}/health")
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=10)

    assert payload["status"] == "ok", output
    assert payload["service"] == "futures-signal", output
    assert payload["worker"]["status"] in {"starting", "idle", "ok"}, output
    assert alias_status == 200, output


def _wait_for_healthcheck(port: int, timeout_seconds: float = 8.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.2)
            continue
    raise AssertionError(f"healthcheck 未在 {timeout_seconds} 秒内返回 200: {last_error}")


def _read_status(url: str) -> int:
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])
