from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from futures_signal.models import FutureQuote, MarketSnapshot, SpotQuote
from futures_signal.validation import QuoteValidationError, QuoteValidator


TZ = ZoneInfo("Asia/Shanghai")


def test_quote_validator_rejects_stale_quotes():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=TZ)
    snapshot = MarketSnapshot(
        timestamp=now,
        fetched_at=now,
        futures={
            "IF": FutureQuote("IF", "IF2606", "IF", 4000, 0.1, 1000, 2000, now - timedelta(minutes=10))
        },
        spots={"IF": SpotQuote("IF", "000300", "沪深300", 4000, 0.1, None, None, now)},
    )

    with pytest.raises(QuoteValidationError):
        QuoteValidator(TZ, max_quote_age_seconds=180).validate(snapshot)


def test_quote_validator_keeps_synchronized_quotes_and_uses_market_ts():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=TZ)
    future_tick = now - timedelta(seconds=20)
    spot_tick = now - timedelta(seconds=10)
    snapshot = MarketSnapshot(
        timestamp=now,
        fetched_at=now,
        futures={"IF": FutureQuote("IF", "IF2606", "IF", 4000, 0.1, 1000, 2000, future_tick)},
        spots={"IF": SpotQuote("IF", "000300", "沪深300", 4000, 0.1, None, None, spot_tick)},
    )

    validated = QuoteValidator(TZ).validate(snapshot)

    assert set(validated.futures) == {"IF"}
    assert validated.timestamp == spot_tick


@pytest.mark.parametrize(
    ("future_tick", "spot_tick"),
    [
        (None, datetime(2026, 5, 27, 10, 0, tzinfo=TZ)),
        (datetime(2026, 5, 27, 10, 0, tzinfo=TZ), None),
        (None, None),
    ],
)
def test_quote_validator_rejects_missing_tick_time(future_tick, spot_tick):
    now = datetime(2026, 5, 27, 10, 0, tzinfo=TZ)
    snapshot = MarketSnapshot(
        timestamp=now,
        fetched_at=now,
        futures={"IF": FutureQuote("IF", "IF2606", "IF", 4000, 0.1, 1000, 2000, future_tick)},
        spots={"IF": SpotQuote("IF", "000300", "沪深300", 4000, 0.1, None, None, spot_tick)},
    )

    with pytest.raises(QuoteValidationError):
        QuoteValidator(TZ).validate(snapshot)
