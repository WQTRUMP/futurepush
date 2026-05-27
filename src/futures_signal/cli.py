from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .ai_commentary import AICommentaryClient
from .akshare_source import AkShareDataSource
from .config import Settings
from .formatting import format_once_output
from .market_calendar import TradingCalendar
from .service import run_forever, run_once, setup_runtime_dirs
from .storage import Storage
from .telegram import TelegramClient


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="futures_signal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="交易时段循环监控并推送")

    once_parser = subparsers.add_parser("once", help="抓取一次并打印当前评分")
    once_parser.add_argument("--push", action="store_true", help="同时按推送规则发送 Telegram")

    subparsers.add_parser("test-telegram", help="发送 Telegram 测试消息")
    subparsers.add_parser("test-ai", help="用 DeepSeek 生成一次 AI 点评测试")
    subparsers.add_parser("init-db", help="初始化 SQLite 表结构")
    subparsers.add_parser("calendar", help="查看今天是否为交易日及下一次采样时间")

    args = parser.parse_args(argv)
    settings = Settings.from_env()
    configure_logging(settings)
    setup_runtime_dirs(settings)

    if args.command == "run":
        run_forever(settings)
        return

    if args.command == "test-telegram":
        client = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        client.send_message("futures-signal Telegram test ok")
        print("Telegram test message sent")
        return

    if args.command == "test-ai":
        from datetime import datetime

        from .models import MarketAnalysis

        sample = MarketAnalysis(
            timestamp=datetime.now(settings.tz),
            score=72,
            band="偏多但不强",
            previous_score=55,
            previous_band="中性震荡",
            components={"basis_change": 78, "open_interest": 68, "relative_strength": 70, "resonance": 75, "tail": 60},
            signals={},
            reasons=["测试：贴水收窄，持仓增加，IF/IC/IM 两个品种走强"],
            warnings=[],
            alert_kind="test",
        )
        commentary = AICommentaryClient(settings).generate(sample)
        print(commentary or "AI commentary disabled")
        return

    if args.command == "calendar":
        from datetime import datetime

        calendar = TradingCalendar(
            settings.tz,
            use_akshare=settings.use_trade_calendar,
            cache_path=settings.trade_calendar_cache_path,
        )
        now = datetime.now(settings.tz)
        trading_day = calendar.is_trading_day(now.date())
        market_open = calendar.is_market_open(now)
        next_session = calendar.seconds_until_next_session(now)
        print(f"now: {now:%Y-%m-%d %H:%M:%S %Z}")
        print(f"calendar_source: {calendar.source}")
        print(f"trading_day: {trading_day}")
        print(f"market_open: {market_open}")
        print(f"next_session_in_seconds: {next_session}")
        if calendar.warning:
            print(f"warning: {calendar.warning}")
        return

    storage = Storage(settings.db_path)
    storage.init()

    if args.command == "once":
        source = AkShareDataSource(settings)
        messenger = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id) if args.push else None
        ai_client = AICommentaryClient(settings) if args.push else None
        analysis, pushed = run_once(settings, storage, source, messenger, ai_client, push=args.push)
        print(format_once_output(analysis))
        print(f"\nTelegram pushed: {pushed}")
        return

    if args.command == "init-db":
        print(f"SQLite initialized: {settings.db_path}")
        return


def configure_logging(settings: Settings) -> None:
    Path("logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/futures_signal.log", encoding="utf-8"),
        ],
    )
