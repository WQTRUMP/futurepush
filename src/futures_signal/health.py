from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Settings

logger = logging.getLogger(__name__)

HEALTH_ENDPOINT_ALIASES = frozenset({"/health", "/healthz"})


@dataclass
class HealthState:
    settings: Settings
    started_at: datetime
    worker_status: str = "starting"
    last_sample_at: datetime | None = None
    last_error_at: datetime | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def mark_ready(self) -> None:
        with self._lock:
            if self.worker_status == "starting":
                self.worker_status = "idle"

    def mark_sample_ok(self, when: datetime) -> None:
        with self._lock:
            self.worker_status = "ok"
            self.last_sample_at = when

    def mark_idle(self) -> None:
        with self._lock:
            if self.worker_status != "starting":
                self.worker_status = "idle"

    def mark_error(self, when: datetime) -> None:
        with self._lock:
            self.worker_status = "error"
            self.last_error_at = when

    def snapshot(self, now: datetime) -> dict[str, object]:
        with self._lock:
            worker_status = self.worker_status
            last_sample_at = self.last_sample_at
            last_error_at = self.last_error_at

        return {
            "status": "ok",
            "service": "futures-signal",
            "time": now.isoformat(),
            "uptime_seconds": max(0, int((now - self.started_at).total_seconds())),
            "worker": {
                "status": worker_status,
                "last_sample_at": last_sample_at.isoformat() if last_sample_at else None,
                "last_error_at": last_error_at.isoformat() if last_error_at else None,
            },
            "storage": _storage_status(self.settings.db_path),
        }


def start_healthcheck_server(settings: Settings, state: HealthState) -> threading.Thread | None:
    if not settings.healthcheck_enabled:
        return None

    server = ThreadingHTTPServer(
        (settings.healthcheck_host, settings.healthcheck_port),
        _build_handler(settings, state),
    )
    thread = threading.Thread(
        target=server.serve_forever,
        name="healthcheck-server",
        daemon=True,
    )
    thread.start()
    logger.info(
        "healthcheck listening on http://%s:%s%s",
        settings.healthcheck_host,
        settings.healthcheck_port,
        settings.healthcheck_path,
    )
    return thread


def _build_handler(settings: Settings, state: HealthState) -> type[BaseHTTPRequestHandler]:
    allowed_paths = set(HEALTH_ENDPOINT_ALIASES)
    allowed_paths.add(settings.healthcheck_path)

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in allowed_paths:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            payload = json.dumps(
                state.snapshot(datetime.now(settings.tz)),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_HEAD(self) -> None:  # noqa: N802
            if self.path not in allowed_paths:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("healthcheck %s - %s", self.address_string(), format % args)

    return HealthHandler


def _storage_status(db_path: Path) -> dict[str, object]:
    exists = db_path.exists()
    result: dict[str, object] = {
        "db_path": str(db_path),
        "db_exists": exists,
        "db_readable": False,
    }
    if not exists:
        return result

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return result

    try:
        connection.execute("SELECT 1")
        result["db_readable"] = True
        return result
    finally:
        connection.close()
