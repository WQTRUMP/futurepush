from __future__ import annotations

from typing import Protocol

from .models import MarketSnapshot


class DataSourceError(RuntimeError):
    pass


class MarketDataSource(Protocol):
    def fetch(self) -> MarketSnapshot:
        ...
