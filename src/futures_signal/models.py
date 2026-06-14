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
    lead_beta: float = 1.0


PRODUCT_CONFIGS: dict[str, ProductConfig] = {
    "IF": ProductConfig("IF", "沪深300指数期货", "000300", "沪深300"),
    "IH": ProductConfig("IH", "上证50指数期货", "000016", "上证50"),
    "IC": ProductConfig("IC", "中证500指数期货", "000905", "中证500"),
    "IM": ProductConfig("IM", "中证1000股指期货", "000852", "中证1000"),
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
class PositionRankSignal:
    product: str
    net_short_top20: int | None
    net_short_change_top20: int | None
    citic_net_short_change: int | None = None
    as_of_date: str | None = None
    lag_days: int | None = None
    is_fallback: bool = False


@dataclass(frozen=True)
class PositionTrendSignal:
    product: str
    days: int
    net_short_change_sum: int
    latest_net_short_change: int | None


@dataclass(frozen=True)
class MarketSnapshot:
    timestamp: datetime
    futures: dict[str, FutureQuote]
    spots: dict[str, SpotQuote]
    terms: dict[str, list[TermQuote]] = field(default_factory=dict)
    positions: dict[str, PositionRankSignal] = field(default_factory=dict)
    position_trends: dict[str, PositionTrendSignal] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fetched_at: datetime | None = None
    source: str = "unknown"
    valid_for_scoring: bool = True


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
    lead_beta: float
    futures_return_5m_pct: float | None
    spot_return_5m_pct: float | None
    lead_residual_5m_pct: float | None
    volume: int
    volume_change: int | None
    volume_change_ratio: float | None
    open_interest: int
    open_interest_change: int | None
    price_change_5m: float | None
    price_oi_signal: str
    main_contract_changed: bool
    daily_price_change: float | None = None
    open_interest_change_ratio: float | None = None
    daily_open_interest_change: int | None = None
    daily_open_interest_change_ratio: float | None = None
    daily_basis_change_bp: float | None = None
    net_short_change_top20: int | None = None
    net_short_change_top20_ratio: float | None = None
    citic_net_short_change: int | None = None
    citic_net_short_change_ratio: float | None = None
    position_rank_lag_days: int | None = None
    position_rank_is_fallback: bool = False


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
    position_trends: dict[str, PositionTrendSignal] = field(default_factory=dict)
    fetched_at: datetime | None = None
    source: str = "unknown"
    valid_for_scoring: bool = True
