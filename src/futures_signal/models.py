from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ProductConfig:
    product: str
    future_name: str
    spot_code: str
    spot_name: str


PRODUCT_CONFIGS: dict[str, ProductConfig] = {
    "IF": ProductConfig("IF", "沪深300指数", "000300", "沪深300"),
    "IH": ProductConfig("IH", "上证50指数", "000016", "上证50"),
    "IC": ProductConfig("IC", "中证500指数", "000905", "中证500"),
    "IM": ProductConfig("IM", "中证1000指数", "000852", "中证1000"),
}

PRODUCTS: tuple[str, ...] = ("IF", "IH", "IC", "IM")
RESONANCE_PRODUCTS: tuple[str, ...] = ("IF", "IC", "IM")


@dataclass(frozen=True)
class FutureQuote:
    product: str
    contract: str
    name: str
    price: float
    change_pct: float
    volume: int
    open_interest: int
    tick_time: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpotQuote:
    product: str
    index_code: str
    name: str
    price: float
    change_pct: float
    volume: float | None
    amount: float | None
    tick_time: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TermQuote:
    product: str
    contract: str
    price: float
    basis: float | None
    basis_bp: float | None
    volume: int | None
    open_interest: int | None
    tick_time: datetime | None


@dataclass(frozen=True)
class MarketSnapshot:
    timestamp: datetime
    futures: dict[str, FutureQuote]
    spots: dict[str, SpotQuote]
    terms: dict[str, list[TermQuote]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HistoricalProductSnapshot:
    timestamp: datetime
    product: str
    contract: str
    futures_price: float
    spot_price: float
    basis_bp: float
    volume: int
    open_interest: int


@dataclass(frozen=True)
class BasisHistoryStats:
    percentile: float | None
    zscore: float | None
    sample_count: int


@dataclass(frozen=True)
class ProductSignal:
    product: str
    product_name: str
    contract: str
    previous_contract: str | None
    futures_price: float
    futures_change_pct: float
    spot_price: float
    spot_change_pct: float
    basis: float
    basis_bp: float
    basis_state: str
    basis_change_bp: float | None
    basis_change_label: str
    basis_percentile: float | None
    basis_zscore: float | None
    basis_history_count: int
    futures_minus_spot_pct: float
    volume: int
    volume_change: int | None
    open_interest: int
    open_interest_change: int | None
    price_change_5m: float | None
    price_oi_signal: str
    main_contract_changed: bool


@dataclass(frozen=True)
class MarketAnalysis:
    timestamp: datetime
    score: int
    band: str
    previous_score: int | None
    previous_band: str | None
    components: dict[str, float]
    signals: dict[str, ProductSignal]
    reasons: list[str]
    warnings: list[str]
    alert_kind: str | None
    term_summary: dict[str, str] = field(default_factory=dict)
