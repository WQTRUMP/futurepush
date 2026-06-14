from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..akshare_providers import ProviderObservation
from ..akshare_utils import brief_error, call_quiet, first_int, rows
from ..config import Settings
from ..models import PRODUCTS, PositionTrendSignal


@dataclass
class PositionTrendProvider:
    ak: Any
    settings: Settings
    _last_position_trend_date: str | None = None
    _last_position_trends: dict[str, PositionTrendSignal] = field(default_factory=dict)

    def fetch(self, now: datetime, warnings: list[str]) -> tuple[dict[str, PositionTrendSignal], ProviderObservation]:
        details: dict[str, Any] = {"as_of_date": now.strftime("%Y%m%d"), "days": self.settings.position_trend_days}
        if not self.settings.fetch_position_rank or self.settings.position_trend_days <= 1:
            return {}, ProviderObservation.skipped("position_trends", details)

        date_text = now.strftime("%Y%m%d")
        if self._last_position_trend_date == date_text:
            details["cache_hit"] = True
            details["products"] = len(self._last_position_trends)
            return self._last_position_trends, ProviderObservation.ok("position_trends", details)

        lookback_days = max(self.settings.position_trend_days * 2, 10)
        start_day = (now.date() - timedelta(days=lookback_days)).strftime("%Y%m%d")
        try:
            df = call_quiet(
                self.ak.get_rank_sum_daily,
                start_day=start_day,
                end_day=date_text,
                vars_list=list(PRODUCTS),
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"近期期货持仓趋势获取失败: {brief_error(exc)}")
            self._last_position_trend_date = date_text
            self._last_position_trends = {}
            details["error"] = type(exc).__name__
            return {}, ProviderObservation.failed("position_trends", details)

        by_product_date: dict[str, dict[str, int]] = {product: {} for product in PRODUCTS}
        for row in rows(df):
            product = str(row.get("variety") or row.get("var") or "").upper()
            if product not in by_product_date:
                continue
            row_date = str(row.get("date") or "")
            if not row_date:
                continue
            long_change = first_int(row, ["long_open_interest_chg_top20"], 0) or 0
            short_change = first_int(row, ["short_open_interest_chg_top20"], 0) or 0
            by_product_date[product][row_date] = by_product_date[product].get(row_date, 0) + short_change - long_change

        trends: dict[str, PositionTrendSignal] = {}
        for product, values_by_date in by_product_date.items():
            recent = sorted(values_by_date.items())[-self.settings.position_trend_days :]
            if not recent:
                continue
            changes = [value for _, value in recent]
            trends[product] = PositionTrendSignal(
                product=product,
                days=len(changes),
                net_short_change_sum=sum(changes),
                latest_net_short_change=changes[-1],
            )

        self._last_position_trend_date = date_text
        self._last_position_trends = trends
        details["products"] = len(trends)
        return trends, ProviderObservation.ok("position_trends", details)
