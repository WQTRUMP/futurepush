from __future__ import annotations

import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, time, timedelta
from io import StringIO
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo


MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)
TAIL_START = time(14, 30)


def is_weekday_market_day(now: datetime) -> bool:
    return now.weekday() < 5


def is_intraday_session(current: time) -> bool:
    return MORNING_START <= current <= MORNING_END or AFTERNOON_START <= current <= AFTERNOON_END


def is_tail_session(now: datetime) -> bool:
    return is_weekday_market_day(now) and TAIL_START <= now.time() <= AFTERNOON_END


def third_friday(year: int, month: int) -> date:
    day = date(year, month, 1)
    friday_offset = (4 - day.weekday()) % 7
    first_friday = day + timedelta(days=friday_offset)
    return first_friday + timedelta(days=14)


def is_roll_window(now: datetime, window_days: int = 7) -> bool:
    delivery = third_friday(now.year, now.month)
    current = now.date()
    return delivery - timedelta(days=window_days) <= current <= delivery


class TradingCalendar:
    def __init__(
        self,
        tz: ZoneInfo,
        use_akshare: bool = True,
        cache_path: Path | None = None,
        fetcher: Callable[[], Iterable[date | datetime | str]] | None = None,
    ):
        self.tz = tz
        self.use_akshare = use_akshare
        self.cache_path = cache_path
        self.fetcher = fetcher
        self.source = "weekday"
        self.warning: str | None = None
        self._dates: set[date] | None = None

    def is_trading_day(self, day: date) -> bool:
        dates = self._trade_dates()
        if dates is None:
            return day.weekday() < 5
        return day in dates

    def is_market_open(self, now: datetime) -> bool:
        local = now.astimezone(self.tz)
        return self.is_trading_day(local.date()) and is_intraday_session(local.time())

    def next_trading_day(self, day: date, max_days: int = 370) -> date:
        candidate = day + timedelta(days=1)
        for _ in range(max_days):
            if self.is_trading_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise RuntimeError("无法在合理范围内找到下一交易日")

    def previous_trading_day(self, day: date, max_days: int = 370) -> date | None:
        candidate = day - timedelta(days=1)
        for _ in range(max_days):
            if self.is_trading_day(candidate):
                return candidate
            candidate -= timedelta(days=1)
        return None

    def seconds_until_next_session(self, now: datetime) -> int:
        local = now.astimezone(self.tz)
        today = local.date()
        for day_offset in range(0, 15):
            day = today + timedelta(days=day_offset)
            if not self.is_trading_day(day):
                continue
            for session_start in (MORNING_START, AFTERNOON_START):
                candidate = datetime.combine(day, session_start, tzinfo=self.tz)
                if candidate > local:
                    return max(1, int((candidate - local).total_seconds()))
        return 3600

    def _trade_dates(self) -> set[date] | None:
        if not self.use_akshare:
            return None
        if self._dates is not None:
            return self._dates

        cached = self._read_cache()
        today = datetime.now(self.tz).date()
        if cached and max(cached) >= today:
            self.source = "cache"
            self._dates = cached
            return self._dates

        try:
            fetched = {_parse_date(value) for value in self._fetch_dates()}
            fetched.discard(None)
            self._dates = {value for value in fetched if value is not None}
            self.source = "akshare"
            self._write_cache(self._dates)
            return self._dates
        except Exception as exc:  # noqa: BLE001
            self.warning = f"交易日历获取失败，已退回工作日判断: {type(exc).__name__}"
            if cached:
                self.source = "stale-cache"
                self._dates = cached
                return self._dates
            self.source = "weekday-fallback"
            return None

    def _fetch_dates(self) -> Iterable[date | datetime | str]:
        if self.fetcher is not None:
            return self.fetcher()
        import akshare as ak

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            df = ak.tool_trade_date_hist_sina()
        return df["trade_date"].tolist()

    def _read_cache(self) -> set[date] | None:
        if self.cache_path is None or not self.cache_path.exists():
            return None
        raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        dates = {_parse_date(item) for item in raw.get("trade_dates", [])}
        dates.discard(None)
        return {value for value in dates if value is not None}

    def _write_cache(self, dates: set[date]) -> None:
        if self.cache_path is None:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.parent.chmod(0o700)
        payload = {"trade_dates": [item.isoformat() for item in sorted(dates)]}
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.cache_path.chmod(0o600)


def _parse_date(value: date | datetime | str | object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def seconds_until_next_session(now: datetime, tz: ZoneInfo) -> int:
    local = now.astimezone(tz)
    candidates: list[datetime] = []
    today = local.date()
    for day_offset in range(0, 8):
        day = today + timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for session_start in (MORNING_START, AFTERNOON_START):
            candidate = datetime.combine(day, session_start, tzinfo=tz)
            if candidate > local:
                candidates.append(candidate)
    if not candidates:
        return 60
    return max(1, int((min(candidates) - local).total_seconds()))
