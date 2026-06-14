from __future__ import annotations

import argparse
import logging
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .ai_commentary import AICommentaryClient
from .akshare_source import AkShareDataSource
from .config import Settings
from .formatting import format_once_output
from .market_calendar import TradingCalendar
from .service import run_forever, run_once, setup_runtime_dirs
from .storage import Storage
from .wecom import WeComClient


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="futures_signal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="交易时段循环监控并推送")

    once_parser = subparsers.add_parser("once", help="抓取一次并打印当前评分")
    once_parser.add_argument("--push", action="store_true", help="同时按推送规则发送企业微信")
    once_parser.add_argument(
        "--save-outside-market",
        action="store_true",
        help="非交易时段也允许写入数据库，用于人工调试",
    )

    subparsers.add_parser("test-wecom", help="发送企业微信测试消息")
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

    if args.command == "test-wecom":
        client = WeComClient(settings.wecom_webhook_url)
        client.send_message("futures-signal 企业微信测试消息")
        print("企业微信测试消息已发送")
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

    storage = Storage(
        settings.db_path,
        calendar=TradingCalendar(
            settings.tz,
            use_akshare=settings.use_trade_calendar,
            cache_path=settings.trade_calendar_cache_path,
        ),
    )
    storage.init()

    if args.command == "once":
        source = AkShareDataSource(settings)
        messenger = WeComClient(settings.wecom_webhook_url) if args.push else None
        ai_client = AICommentaryClient(settings) if args.push else None
        analysis, pushed = run_once(
            settings,
            storage,
            source,
            messenger,
            ai_client,
            push=args.push,
            save_outside_market=args.save_outside_market,
        )
        print(format_once_output(analysis))
        print(f"\nWeCom pushed: {pushed}")
        return

    if args.command == "init-db":
        print(f"SQLite initialized: {settings.db_path}")
        return


def configure_logging(settings: Settings) -> None:
    Path("logs").mkdir(parents=True, exist_ok=True)
    Path("logs").chmod(0o700)
    log_path = Path("logs/futures_signal.log")
    log_path.touch(exist_ok=True)
    log_path.chmod(0o600)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    redaction_filter = _SensitiveDataFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redaction_filter)


class _SensitiveDataFilter(logging.Filter):
    SENSITIVE_QUERY_KEYS = {"key", "token", "api_key", "authorization"}

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            record.args = tuple(self._redact(value) if isinstance(value, str) else value for value in record.args)
        return True

    def _redact(self, value: str) -> str:
        if "http://" not in value and "https://" not in value:
            return value
        parts = urlsplit(value)
        if not parts.query:
            return value
        redacted_query = urlencode(
            [
                (key, "***" if key.lower() in self.SENSITIVE_QUERY_KEYS else item)
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, redacted_query, parts.fragment))
