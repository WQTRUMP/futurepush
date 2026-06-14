from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..akshare_providers import ProviderObservation
from ..akshare_utils import brief_error, call_quiet, first_int, product_from_contract, rows
from ..config import Settings
from ..market_calendar import TradingCalendar
from ..models import PRODUCTS, PRODUCT_CONFIGS, PositionRankSignal

_REAL_DATETIME = datetime


@dataclass
class PositionRankProvider:
    ak: Any
    settings: Settings
    calendar: TradingCalendar
    _last_position_date: str | None = None
    _last_positions: dict[str, PositionRankSignal] = field(default_factory=dict)
    _last_position_empty_at: datetime | None = None

    def fetch(self, now: datetime, warnings: list[str]) -> tuple[dict[str, PositionRankSignal], ProviderObservation]:
        details: dict[str, Any] = {"as_of_date": now.strftime("%Y%m%d")}
        if not self.settings.fetch_position_rank:
            return {}, ProviderObservation.skipped("positions", details)

        date_text = now.strftime("%Y%m%d")
        if self._last_position_date == date_text and self._last_positions:
            details["cache_hit"] = True
            return self._last_positions, ProviderObservation.ok("positions", details)

        if self._last_position_empty_at is not None:
            age = (now - self._last_position_empty_at).total_seconds()
            if age < self.settings.position_rank_empty_retry_seconds:
                details["cooldown_active"] = True
                details["cache_hit"] = bool(self._last_positions)
                if self._last_positions:
                    warnings.append("今日持仓排名暂不可用，继续使用上一可用交易日排名")
                    details["fallback"] = True
                    return self._last_positions, ProviderObservation.degraded("positions", details)
                warnings.append("今日持仓排名暂不可用，等待下次重试")
                return {}, ProviderObservation.degraded("positions", details)

        positions = self._fetch_positions_for_date(date_text, now, warnings)
        if positions:
            self._last_position_date = date_text
            self._last_positions = positions
            self._last_position_empty_at = None
            details["products"] = len(positions)
            return positions, ProviderObservation.ok("positions", details)

        self._last_position_empty_at = now
        fallback = self._fetch_previous_available_positions(now, warnings)
        details["cooldown_active"] = False
        if fallback:
            self._last_positions = fallback
            details["fallback"] = True
            details["products"] = len(fallback)
            details["as_of_date"] = next(iter(fallback.values())).as_of_date
            return fallback, ProviderObservation.degraded("positions", details)
        return {}, ProviderObservation.degraded("positions", details)

    def _fetch_positions_for_date(
        self,
        date_text: str,
        now: datetime,
        warnings: list[str],
    ) -> dict[str, PositionRankSignal]:
        try:
            rank_sum = call_quiet(self.ak.get_rank_sum, date=date_text, vars_list=list(PRODUCTS))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"中金所持仓汇总获取失败: {brief_error(exc)}")
            return {}

        citic_changes = self._fetch_citic_net_short_changes(date_text, warnings)
        position_rows = rows(rank_sum)
        positions: dict[str, PositionRankSignal] = {}
        for product in PRODUCTS:
            product_rows = [row for row in position_rows if str(row.get("variety") or row.get("var") or "").upper() == product]
            if not product_rows:
                continue
            long_total = sum(first_int(row, ["long_open_interest_top20"], 0) or 0 for row in product_rows)
            short_total = sum(first_int(row, ["short_open_interest_top20"], 0) or 0 for row in product_rows)
            long_change = sum(first_int(row, ["long_open_interest_chg_top20"], 0) or 0 for row in product_rows)
            short_change = sum(first_int(row, ["short_open_interest_chg_top20"], 0) or 0 for row in product_rows)
            positions[product] = PositionRankSignal(
                product=product,
                net_short_top20=short_total - long_total,
                net_short_change_top20=short_change - long_change,
                citic_net_short_change=citic_changes.get(product),
                as_of_date=date_text,
                lag_days=max(0, (now.date() - _REAL_DATETIME.strptime(date_text, "%Y%m%d").date()).days),
                is_fallback=date_text != now.strftime("%Y%m%d"),
            )
        return positions

    def _fetch_previous_available_positions(
        self,
        now: datetime,
        warnings: list[str],
        max_lookback_days: int = 7,
    ) -> dict[str, PositionRankSignal]:
        for day_offset in range(1, max_lookback_days + 1):
            candidate = now - timedelta(days=day_offset)
            if not self.calendar.is_trading_day(candidate.date()):
                continue
            date_text = candidate.strftime("%Y%m%d")
            positions = self._fetch_positions_for_date(date_text, now, [])
            if positions:
                warnings.append(f"今日持仓排名暂不可用，已使用 {date_text} 排名")
                self._last_position_date = date_text
                return positions
        warnings.append("今日及上一可用交易日持仓排名均不可用，本次持仓排名降为中性")
        return {}

    def _fetch_citic_net_short_changes(self, date_text: str, warnings: list[str]) -> dict[str, int]:
        try:
            rank_table = call_quiet(self.ak.get_cffex_rank_table, date=date_text, vars_list=list(PRODUCTS))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"中信期货席位持仓获取失败: {brief_error(exc)}")
            return {}
        if not isinstance(rank_table, dict):
            return {}
        result: dict[str, int] = {}
        for contract, df in rank_table.items():
            product = product_from_contract(contract)
            if product not in PRODUCT_CONFIGS:
                continue
            long_change = 0
            short_change = 0
            for row in rows(df):
                if "中信期货" in str(row.get("long_party_name") or ""):
                    long_change += first_int(row, ["long_open_interest_chg"], 0) or 0
                if "中信期货" in str(row.get("short_party_name") or ""):
                    short_change += first_int(row, ["short_open_interest_chg"], 0) or 0
            result[product] = result.get(product, 0) + short_change - long_change
        return result
