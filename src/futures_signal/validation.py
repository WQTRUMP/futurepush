from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import MarketSnapshot, PRODUCTS


class QuoteValidationError(RuntimeError):
    pass


class QuoteValidator:
    def __init__(
        self,
        tz: ZoneInfo,
        max_quote_age_seconds: int = 180,
        max_tick_sync_seconds: int = 60,
    ):
        self.tz = tz
        self.max_quote_age_seconds = max_quote_age_seconds
        self.max_tick_sync_seconds = max_tick_sync_seconds

    def validate(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        fetched_at = _localize(snapshot.fetched_at or snapshot.timestamp, self.tz)
        warnings = list(snapshot.warnings)
        futures = {}
        spots = {}
        terms = {}
        positions = {}
        market_times: list[datetime] = []

        for product in PRODUCTS:
            future = snapshot.futures.get(product)
            spot = snapshot.spots.get(product)
            if future is None or spot is None:
                continue

            future_tick = _localize(future.tick_time, self.tz)
            spot_tick = _localize(spot.tick_time, self.tz)
            invalid_reasons = []

            if _is_stale(fetched_at, future_tick, self.max_quote_age_seconds):
                invalid_reasons.append("期货tick陈旧")
            if _is_stale(fetched_at, spot_tick, self.max_quote_age_seconds):
                invalid_reasons.append("现货tick陈旧")
            if _is_out_of_sync(future_tick, spot_tick, self.max_tick_sync_seconds):
                invalid_reasons.append("期现tick不同步")

            if invalid_reasons:
                warnings.append(f"{product} 数据未参与评分: {','.join(invalid_reasons)}")
                continue

            futures[product] = future
            spots[product] = spot
            terms[product] = snapshot.terms.get(product, [])
            if product in snapshot.positions:
                positions[product] = snapshot.positions[product]
            market_times.extend(value for value in (future_tick, spot_tick) if value is not None)

        if not futures or not spots:
            raise QuoteValidationError("无有效期现同步行情，已拒绝生成评分")

        market_ts = max(market_times) if market_times else fetched_at
        return replace(
            snapshot,
            timestamp=market_ts,
            futures=futures,
            spots=spots,
            terms=terms,
            positions=positions,
            warnings=warnings,
            fetched_at=fetched_at,
            valid_for_scoring=True,
        )


def _localize(value: datetime | None, tz: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _is_stale(fetched_at: datetime, tick_time: datetime | None, max_age_seconds: int) -> bool:
    if tick_time is None:
        return False
    return (fetched_at - tick_time).total_seconds() > max_age_seconds


def _is_out_of_sync(
    future_tick: datetime | None,
    spot_tick: datetime | None,
    max_sync_seconds: int,
) -> bool:
    if future_tick is None or spot_tick is None:
        return False
    return abs((future_tick - spot_tick).total_seconds()) > max_sync_seconds
