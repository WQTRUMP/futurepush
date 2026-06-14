from __future__ import annotations

import sqlite3
from pathlib import Path

from ..market_calendar import TradingCalendar
from .shared import connect_sqlite, initialize_schema


class SqliteRepository:
    def __init__(self, db_path: Path, calendar: TradingCalendar | None = None):
        self.db_path = db_path
        self.calendar = calendar

    def init(self) -> None:
        initialize_schema(self.db_path)

    @property
    def calendar_source(self) -> str:
        return self.calendar.source if self.calendar is not None else "weekday"

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.db_path)
