import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from futures_signal.market_calendar import TradingCalendar, is_roll_window, third_friday


TZ = ZoneInfo("Asia/Shanghai")


def test_third_friday_and_roll_window():
    assert third_friday(2026, 5) == date(2026, 5, 15)
    assert is_roll_window(datetime(2026, 5, 14, 10, 0, tzinfo=TZ), window_days=7)
    assert not is_roll_window(datetime(2026, 5, 7, 10, 0, tzinfo=TZ), window_days=7)


def test_trading_calendar_uses_fetched_dates(tmp_path):
    calendar = TradingCalendar(
        TZ,
        use_akshare=True,
        cache_path=tmp_path / "trade_dates.json",
        fetcher=lambda: [date(2026, 5, 27)],
    )
    assert calendar.is_trading_day(date(2026, 5, 27))
    assert not calendar.is_trading_day(date(2026, 5, 28))
    assert calendar.source == "akshare"


def test_trading_calendar_writes_cache_with_restricted_permissions(tmp_path):
    cache_path = tmp_path / "nested" / "trade_dates.json"
    calendar = TradingCalendar(
        TZ,
        use_akshare=True,
        cache_path=cache_path,
        fetcher=lambda: [date(2026, 5, 27), date(2026, 5, 28)],
    )

    assert calendar.is_trading_day(date(2026, 5, 27))

    assert oct(os.stat(cache_path.parent).st_mode & 0o777) == "0o700"
    assert oct(os.stat(cache_path).st_mode & 0o777) == "0o600"


def test_trading_calendar_next_and_previous_trading_day(tmp_path):
    calendar = TradingCalendar(
        TZ,
        use_akshare=True,
        cache_path=tmp_path / "trade_dates.json",
        fetcher=lambda: [date(2026, 5, 28), date(2026, 6, 1)],
    )

    assert calendar.next_trading_day(date(2026, 5, 28)) == date(2026, 6, 1)
    assert calendar.previous_trading_day(date(2026, 6, 1)) == date(2026, 5, 28)
    assert calendar.previous_trading_day(date(2026, 5, 28), max_days=1) is None


def test_trading_calendar_next_trading_day_raises_when_missing(tmp_path):
    calendar = TradingCalendar(
        TZ,
        use_akshare=True,
        cache_path=tmp_path / "trade_dates.json",
        fetcher=lambda: [date(2026, 5, 28)],
    )

    with pytest.raises(RuntimeError, match="无法在合理范围内找到下一交易日"):
        calendar.next_trading_day(date(2026, 5, 28), max_days=1)
