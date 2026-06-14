from __future__ import annotations

from datetime import datetime
from typing import Any

from .akshare_providers import AkshareClientAdapter, FetchObservation, RealtimeQuoteBundleProvider
from .akshare_utils import brief_error, call_quiet, first_float, first_int, infer_contract, infer_product, product_from_contract, rows
from .composite_source import CompositeMarketDataSource
from .config import Settings
from .data_sources import DataSourceError
from .market_calendar import TradingCalendar


class AkShareDataSource:
    _DELEGATED_ATTRS = {
        "ak",
        "quote_bundle_provider",
        "calendar",
        "_last_term_fetch_at",
        "_last_terms",
        "last_fetch_observation",
        "position_rank_provider",
        "position_trend_provider",
    }

    def __init__(self, settings: Settings):
        object.__setattr__(self, "settings", settings)
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataSourceError("未安装 akshare，请先运行 pip install -e .") from exc

        quote_bundle_provider = RealtimeQuoteBundleProvider(
            AkshareClientAdapter(
                ak=ak,
                rows_parser=rows,
                quiet_caller=call_quiet,
            )
        )
        calendar = TradingCalendar(
            settings.tz,
            use_akshare=settings.use_trade_calendar,
            cache_path=settings.trade_calendar_cache_path,
        )
        object.__setattr__(
            self,
            "_impl",
            CompositeMarketDataSource(
                settings=settings,
                ak=ak,
                quote_bundle_provider=quote_bundle_provider,
                calendar=calendar,
            ),
        )

    def __getattr__(self, name: str) -> Any:
        if name in {"_last_position_date", "_last_positions", "_last_position_empty_at"}:
            return getattr(self._impl.position_rank_provider, name)
        if name in {"_last_position_trend_date", "_last_position_trends"}:
            return getattr(self._impl.position_trend_provider, name)
        if name == "_fetch_positions_if_due":
            return getattr(self._impl.position_rank_provider, "fetch")
        if name in {"_fetch_positions_for_date", "_fetch_previous_available_positions", "_fetch_citic_net_short_changes"}:
            return getattr(self._impl.position_rank_provider, name)
        if name == "_fetch_position_trends_if_due":
            return getattr(self._impl.position_trend_provider, "fetch")
        return getattr(self._impl, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"settings", "_impl"}:
            object.__setattr__(self, name, value)
            return
        if name in {"_last_position_date", "_last_positions", "_last_position_empty_at"}:
            setattr(self._impl.position_rank_provider, name, value)
            return
        if name in {"_last_position_trend_date", "_last_position_trends"}:
            setattr(self._impl.position_trend_provider, name, value)
            return
        if name in self._DELEGATED_ATTRS:
            setattr(self._impl, name, value)
            return
        object.__setattr__(self, name, value)

    def fetch(self):
        return self._impl.fetch()

    @property
    def last_fetch_observation(self) -> FetchObservation:
        return self._impl.last_fetch_observation

    def _fetch_positions_if_due(self, now, warnings):
        return self._impl.position_rank_provider.fetch(now, warnings)[0]

    def _fetch_positions_for_date(self, date_text, now, warnings):
        return self._impl.position_rank_provider._fetch_positions_for_date(date_text, now, warnings)

    def _fetch_previous_available_positions(self, now, warnings, max_lookback_days=7):
        return self._impl.position_rank_provider._fetch_previous_available_positions(
            now,
            warnings,
            max_lookback_days=max_lookback_days,
        )

    def _fetch_citic_net_short_changes(self, date_text, warnings):
        return self._impl.position_rank_provider._fetch_citic_net_short_changes(date_text, warnings)

    def _fetch_position_trends_if_due(self, now, warnings):
        return self._impl.position_trend_provider.fetch(now, warnings)[0]

    @staticmethod
    def _rows(df: Any) -> list[dict[str, Any]]:
        return rows(df)

    @staticmethod
    def _call_quiet(func: Any, *args: Any, **kwargs: Any) -> Any:
        return call_quiet(func, *args, **kwargs)

    @staticmethod
    def _brief_error(exc: Exception, limit: int = 180) -> str:
        return brief_error(exc, limit=limit)

    @staticmethod
    def _product_from_contract(value: str) -> str | None:
        return product_from_contract(value)

    @classmethod
    def _infer_product(cls, row: dict[str, Any]) -> str | None:
        return infer_product(row)

    @staticmethod
    def _infer_contract(row: dict[str, Any]) -> str | None:
        return infer_contract(row)

    @staticmethod
    def _first_float(row: dict[str, Any], keys: list[str], default: float | None = 0.0) -> float | None:
        return first_float(row, keys, default)

    @staticmethod
    def _first_int(row: dict[str, Any], keys: list[str], default: int | None = 0) -> int | None:
        return first_int(row, keys, default)


def build_akshare_data_source(settings: Settings) -> AkShareDataSource:
    return AkShareDataSource(settings)
