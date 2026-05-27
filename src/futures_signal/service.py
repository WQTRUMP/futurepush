from __future__ import annotations

import logging
import time
from datetime import datetime

from .ai_commentary import AICommentaryClient, AICommentaryError
from .akshare_source import AkShareDataSource
from .config import Settings
from .data_sources import MarketDataSource
from .formatting import format_analysis
from .market_calendar import TradingCalendar
from .models import MarketAnalysis
from .scoring import analyze_market
from .storage import Storage
from .telegram import TelegramClient

logger = logging.getLogger(__name__)


def run_once(
    settings: Settings,
    storage: Storage,
    source: MarketDataSource,
    messenger: TelegramClient | None = None,
    ai_client: AICommentaryClient | None = None,
    push: bool = True,
) -> tuple[MarketAnalysis, bool]:
    snapshot = source.fetch()
    references = {
        product: storage.get_reference_snapshot(product, snapshot.timestamp)
        for product in ("IF", "IH", "IC", "IM")
    }
    basis_histories = {
        product: storage.get_basis_history(product, snapshot.timestamp, settings.basis_history_days)
        for product in ("IF", "IH", "IC", "IM")
    }
    latest_contracts = storage.latest_contracts()
    previous_score, previous_band = storage.latest_score()
    analysis = analyze_market(
        snapshot,
        references,
        latest_contracts,
        previous_score,
        previous_band,
        basis_histories=basis_histories,
        dividend_season_adjust=settings.dividend_season_adjust,
        roll_window_days=settings.roll_window_days,
    )
    storage.save_analysis(analysis)

    should_push = push and _should_push(settings, storage, analysis)
    if should_push:
        ai_commentary = _generate_ai_commentary(ai_client, analysis)
        message = format_analysis(analysis, ai_commentary=ai_commentary)
        if messenger is None:
            messenger = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        messenger.send_message(message)
        storage.save_alert(
            analysis.timestamp,
            _alert_kind(settings, analysis),
            analysis.band,
            analysis.score,
            message,
        )
    return analysis, should_push


def run_forever(settings: Settings) -> None:
    setup_runtime_dirs(settings)
    storage = Storage(settings.db_path)
    storage.init()
    source = AkShareDataSource(settings)
    messenger = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    ai_client = AICommentaryClient(settings)
    calendar = TradingCalendar(
        settings.tz,
        use_akshare=settings.use_trade_calendar,
        cache_path=settings.trade_calendar_cache_path,
    )

    logger.info("futures-signal started")
    while True:
        now = datetime.now(settings.tz)
        if settings.run_outside_market_hours or calendar.is_market_open(now):
            try:
                analysis, pushed = run_once(settings, storage, source, messenger, ai_client, push=True)
                logger.info("sample score=%s band=%s pushed=%s", analysis.score, analysis.band, pushed)
            except Exception:
                logger.exception("sampling failed")
            time.sleep(settings.sample_interval_seconds)
            continue

        wait_seconds = min(calendar.seconds_until_next_session(now), 3600)
        if calendar.warning:
            logger.warning(calendar.warning)
        logger.info(
            "market closed, calendar_source=%s trading_day=%s sleeping=%ss",
            calendar.source,
            calendar.is_trading_day(now.date()),
            wait_seconds,
        )
        time.sleep(wait_seconds)


def setup_runtime_dirs(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)


def _should_push(settings: Settings, storage: Storage, analysis: MarketAnalysis) -> bool:
    if settings.push_every_sample:
        kind = _alert_kind(settings, analysis)
        return not storage.has_recent_alert(kind, analysis.timestamp, settings.alert_cooldown_seconds)

    if analysis.alert_kind is None:
        return False

    kind = _alert_kind(settings, analysis)
    return not storage.has_recent_alert(kind, analysis.timestamp, settings.alert_cooldown_seconds)


def _alert_kind(settings: Settings, analysis: MarketAnalysis) -> str:
    if settings.push_every_sample:
        return "sample"
    return analysis.alert_kind or "sample"


def _generate_ai_commentary(ai_client: AICommentaryClient | None, analysis: MarketAnalysis) -> str | None:
    if ai_client is None:
        return None
    try:
        return ai_client.generate(analysis)
    except AICommentaryError as exc:
        logger.warning("AI commentary failed: %s", exc)
        return f"AI点评暂不可用：{exc}"
