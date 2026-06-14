from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime

from .ai_commentary import AICommentaryClient, AICommentaryError
from .akshare_source import build_akshare_data_source
from .config import Settings
from .data_sources import MarketDataSource
from .formatting import format_analysis
from .health import HealthState, start_healthcheck_server
from .market_calendar import TradingCalendar
from .models import MarketAnalysis, MarketSnapshot, PRODUCTS
from .repositories import AnalysisWriteRepository, MarketReadRepository, PredictionRepository
from .scoring import analyze_market
from .storage import Storage
from .validation import QuoteValidator
from .wecom import WeComClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SamplingContext:
    snapshot: MarketSnapshot
    should_persist: bool
    references: dict[str, object | None]
    daily_references: dict[str, object | None]
    basis_histories: dict[str, list[float]]
    latest_contracts: dict[str, str]
    previous_score: int | None
    previous_band: str | None


def run_once(
    settings: Settings,
    market_reads: MarketReadRepository,
    analysis_writes: AnalysisWriteRepository,
    predictions: PredictionRepository,
    source: MarketDataSource,
    messenger: WeComClient | None = None,
    ai_client: AICommentaryClient | None = None,
    push: bool = True,
    save_outside_market: bool = False,
    calendar: TradingCalendar | None = None,
) -> tuple[MarketAnalysis, bool]:
    calendar = calendar or TradingCalendar(
        settings.tz,
        use_akshare=settings.use_trade_calendar,
        cache_path=settings.trade_calendar_cache_path,
    )
    context = build_sampling_context(
        settings,
        market_reads,
        source,
        calendar,
        save_outside_market,
    )
    analysis = execute_analysis(settings, context)
    persist_analysis_side_effects(analysis_writes, predictions, analysis, context.should_persist)
    should_push = dispatch_alert_if_needed(
        settings,
        analysis_writes,
        analysis,
        context.should_persist,
        push=push,
        messenger=messenger,
        ai_client=ai_client,
    )
    return analysis, should_push


def build_sampling_context(
    settings: Settings,
    market_reads: MarketReadRepository,
    source: MarketDataSource,
    calendar: TradingCalendar,
    save_outside_market: bool,
) -> SamplingContext:
    fetched_snapshot = source.fetch()
    snapshot, should_persist = _prepare_snapshot(settings, fetched_snapshot, calendar, save_outside_market)
    references = {
        product: market_reads.get_reference_snapshot(product, snapshot.timestamp)
        for product in PRODUCTS
    }
    daily_references = {
        product: market_reads.get_daily_reference_snapshot(product, snapshot.timestamp)
        for product in PRODUCTS
    }
    basis_histories = {
        product: market_reads.get_basis_history(product, snapshot.timestamp, settings.basis_history_days)
        for product in PRODUCTS
    }
    latest_contracts = market_reads.latest_contracts()
    previous_score, previous_band = market_reads.latest_score()
    return SamplingContext(
        snapshot=snapshot,
        should_persist=should_persist,
        references=references,
        daily_references=daily_references,
        basis_histories=basis_histories,
        latest_contracts=latest_contracts,
        previous_score=previous_score,
        previous_band=previous_band,
    )


def execute_analysis(settings: Settings, context: SamplingContext) -> MarketAnalysis:
    return analyze_market(
        context.snapshot,
        context.references,
        context.latest_contracts,
        context.previous_score,
        context.previous_band,
        basis_histories=context.basis_histories,
        daily_references=context.daily_references,
        dividend_season_adjust=settings.dividend_season_adjust,
        roll_window_days=settings.roll_window_days,
    )


def persist_analysis_side_effects(
    analysis_writes: AnalysisWriteRepository,
    predictions: PredictionRepository,
    analysis: MarketAnalysis,
    should_persist: bool,
) -> None:
    if not should_persist:
        return
    analysis_writes.save_analysis(analysis)
    predictions.enqueue_predictions(analysis)


def dispatch_alert_if_needed(
    settings: Settings,
    analysis_writes: AnalysisWriteRepository,
    analysis: MarketAnalysis,
    should_persist: bool,
    *,
    push: bool,
    messenger: WeComClient | None,
    ai_client: AICommentaryClient | None,
) -> bool:
    kind = _alert_kind(settings, analysis)
    should_push = should_persist and push and kind is not None and not analysis_writes.has_recent_alert(
        kind,
        analysis.timestamp,
        _alert_cooldown_seconds(settings, kind),
    )
    if not should_push:
        return False

    ai_commentary = _generate_ai_commentary(ai_client, analysis)
    message = format_analysis(
        analysis,
        ai_commentary=ai_commentary,
        include_position_trend=_is_last_daily_window(settings, analysis.timestamp, kind),
    )
    if messenger is None:
        messenger = WeComClient(settings.wecom_webhook_url)
    messenger.send_message(message)
    analysis_writes.save_alert(
        analysis.timestamp,
        kind,
        analysis.band,
        analysis.score,
        message,
    )
    return True


def run_forever(settings: Settings) -> None:
    setup_runtime_dirs(settings)
    health_state = HealthState(settings=settings, started_at=datetime.now(settings.tz))
    start_healthcheck_server(settings, health_state)
    calendar = TradingCalendar(
        settings.tz,
        use_akshare=settings.use_trade_calendar,
        cache_path=settings.trade_calendar_cache_path,
    )
    storage = Storage(settings.db_path, calendar=calendar)
    storage.init()
    source = build_akshare_data_source(settings)
    messenger = WeComClient(settings.wecom_webhook_url)
    ai_client = AICommentaryClient(settings)
    health_state.mark_ready()

    logger.info("futures-signal started")
    while True:
        now = datetime.now(settings.tz)
        if settings.run_outside_market_hours or calendar.is_market_open(now):
            try:
                analysis, pushed = run_once(
                    settings,
                    storage.market_reads,
                    storage.analysis_writes,
                    storage.predictions,
                    source,
                    messenger,
                    ai_client,
                    push=True,
                    calendar=calendar,
                )
                health_state.mark_sample_ok(analysis.timestamp)
                logger.info("sample score=%s band=%s pushed=%s", analysis.score, analysis.band, pushed)
            except Exception:
                health_state.mark_error(now)
                logger.exception("sampling failed")
            time.sleep(settings.sample_interval_seconds)
            continue

        health_state.mark_idle()
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
    settings.data_dir.chmod(0o700)
    settings.db_path.parent.chmod(0o700)


def _prepare_snapshot(
    settings: Settings,
    snapshot: MarketSnapshot,
    calendar: TradingCalendar,
    save_outside_market: bool,
) -> tuple[MarketSnapshot, bool]:
    fetched_at = snapshot.fetched_at or snapshot.timestamp
    outside_market = not settings.run_outside_market_hours and not calendar.is_market_open(fetched_at)
    if outside_market and not save_outside_market:
        warning = "非交易时段样本仅展示，未入库也不会推送"
        return (
            replace(
                snapshot,
                fetched_at=fetched_at,
                warnings=[*snapshot.warnings, warning],
                valid_for_scoring=False,
            ),
            False,
        )

    validator = QuoteValidator(
        settings.tz,
        max_quote_age_seconds=settings.max_quote_age_seconds,
        max_tick_sync_seconds=settings.max_tick_sync_seconds,
    )
    return validator.validate(snapshot), True


def _should_push(settings: Settings, analysis_writes: AnalysisWriteRepository, analysis: MarketAnalysis) -> bool:
    kind = _alert_kind(settings, analysis)
    if kind is None:
        return False
    return not analysis_writes.has_recent_alert(kind, analysis.timestamp, _alert_cooldown_seconds(settings, kind))


def _is_last_daily_window(settings: Settings, now: datetime, kind: str | None) -> bool:
    if kind is None or not kind.startswith("daily_") or kind.startswith("daily_urgent_"):
        return False
    last_time = _last_daily_push_time(settings)
    if last_time is None:
        return False
    return kind == f"daily_{now:%Y%m%d}_{last_time}"


def _last_daily_push_time(settings: Settings) -> str | None:
    valid: list[tuple[int, int]] = []
    for text in settings.daily_push_times.split(","):
        text = text.strip()
        if not text:
            continue
        try:
            hour_text, minute_text = text.split(":", 1)
            valid.append((int(hour_text), int(minute_text)))
        except ValueError:
            continue
    if not valid:
        return None
    hour, minute = max(valid)
    return f"{hour:02d}{minute:02d}"


def _alert_kind(settings: Settings, analysis: MarketAnalysis) -> str | None:
    if settings.push_every_sample:
        return "sample"

    if settings.push_policy == "event":
        return analysis.alert_kind

    if settings.push_policy == "daily":
        scheduled_kind = _daily_window_kind(settings, analysis.timestamp)
        if scheduled_kind:
            return scheduled_kind
        return _daily_urgent_kind(analysis)

    return analysis.alert_kind


def _alert_cooldown_seconds(settings: Settings, kind: str) -> int:
    if kind.startswith("daily_urgent_"):
        return settings.urgent_alert_cooldown_seconds
    if kind.startswith("daily_"):
        return settings.daily_alert_cooldown_seconds
    return settings.alert_cooldown_seconds


def _daily_window_kind(settings: Settings, now: datetime) -> str | None:
    for text in settings.daily_push_times.split(","):
        text = text.strip()
        if not text:
            continue
        try:
            hour_text, minute_text = text.split(":", 1)
            target = now.replace(hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0)
        except ValueError:
            logger.warning("invalid DAILY_PUSH_TIMES item: %s", text)
            continue
        delta_seconds = (now - target).total_seconds()
        if 0 <= delta_seconds < settings.daily_push_window_seconds:
            return f"daily_{now:%Y%m%d}_{target:%H%M}"
    return None


def _daily_urgent_kind(analysis: MarketAnalysis) -> str | None:
    if analysis.alert_kind == "strong_long" or analysis.score >= 80:
        return "daily_urgent_bullish"
    if analysis.alert_kind == "strong_short" or analysis.score <= 19:
        return "daily_urgent_bearish"
    return None


def _generate_ai_commentary(ai_client: AICommentaryClient | None, analysis: MarketAnalysis) -> str | None:
    if ai_client is None:
        return None
    try:
        return ai_client.generate(analysis)
    except AICommentaryError as exc:
        logger.warning("AI commentary failed: %s", exc)
        return "AI点评暂不可用，请查看系统日志。"
