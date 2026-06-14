from __future__ import annotations

import logging
import re
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from typing import Any

from .config import Settings
from .data_sources import DataSourceError
from .market_calendar import TradingCalendar
from .metrics import basis, basis_bp, contract_sort_key, normalize_index_code, to_float, to_int
from .models import (
    FutureQuote,
    MarketSnapshot,
    PositionRankSignal,
    PositionTrendSignal,
    PRODUCT_CONFIGS,
    PRODUCTS,
    SpotQuote,
    TermQuote,
)

logger = logging.getLogger(__name__)


class AkShareDataSource:
    def __init__(self, settings: Settings):
        self.settings = settings
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataSourceError("未安装 akshare，请先运行 pip install -e .") from exc
        self.ak = ak
        self._last_term_fetch_at: datetime | None = None
        self._last_terms: dict[str, list[TermQuote]] = {}
        self._last_position_date: str | None = None
        self._last_positions: dict[str, PositionRankSignal] = {}
        self._last_position_empty_at: datetime | None = None
        self._last_position_trend_date: str | None = None
        self._last_position_trends: dict[str, PositionTrendSignal] = {}
        self.calendar = TradingCalendar(
            settings.tz,
            use_akshare=settings.use_trade_calendar,
            cache_path=settings.trade_calendar_cache_path,
        )
        self._realtime_cache: dict[str, list[dict[str, Any]]] = {}

    def fetch(self) -> MarketSnapshot:
        now = datetime.now(self.settings.tz)
        warnings: list[str] = []
        self._realtime_cache = {}
        try:
            futures = self._fetch_main_futures(now, warnings)
            spots = self._fetch_spots(now, warnings)
            terms = self._fetch_terms_if_due(now, spots, warnings)
            positions = self._fetch_positions_if_due(now, warnings)
            position_trends = self._fetch_position_trends_if_due(now, warnings)
        finally:
            self._realtime_cache = {}

        if not futures:
            raise DataSourceError("AkShare 未返回 IF/IH/IC/IM 期货主力行情")
        if not spots:
            raise DataSourceError("AkShare 未返回对应现货指数行情")

        missing = [product for product in PRODUCTS if product not in futures or product not in spots]
        if missing:
            warnings.append(f"部分品种缺失: {','.join(missing)}")

        return MarketSnapshot(
            timestamp=now,
            futures=futures,
            spots=spots,
            terms=terms,
            positions=positions,
            position_trends=position_trends,
            warnings=warnings,
            fetched_at=now,
            source="akshare",
        )

    def _fetch_main_futures(self, now: datetime, warnings: list[str]) -> dict[str, FutureQuote]:
        contracts = self._main_contract_symbols(warnings)
        if not contracts:
            contracts = ["IF0", "IH0", "IC0", "IM0"]
            warnings.append("主力合约列表不可用，已回退到新浪连续合约 IF0/IH0/IC0/IM0")

        result = self._fetch_main_futures_from_realtime(now, contracts, warnings)
        if all(product in result for product in PRODUCTS):
            return result

        symbol_text = ",".join(contracts)
        try:
            df = self._call_quiet(self.ak.futures_zh_spot, symbol=symbol_text, market="FF", adjust="0")
        except Exception as exc:  # noqa: BLE001 - third-party API can raise many exception types
            warnings.append(f"主力期货新浪批量补充源获取失败: {self._brief_error(exc)}")
            return result

        rows = self._rows(df)
        for index, row in enumerate(rows):
            product = self._infer_product(row)
            if product is None and index < len(contracts):
                product = self._product_from_contract(contracts[index])
            if product not in PRODUCT_CONFIGS:
                continue
            contract = self._infer_contract(row) or (contracts[index] if index < len(contracts) else product)
            price = self._first_float(row, ["current_price", "trade", "最新价", "price"])
            if price <= 0:
                continue
            result[product] = self._future_quote_from_row(now, row, product, contract, price)
        if not result:
            warnings.append("主力期货实时源和新浪批量补充源均返回空数据")
        return result

    def _fetch_main_futures_from_realtime(
        self,
        now: datetime,
        contracts: list[str],
        warnings: list[str],
    ) -> dict[str, FutureQuote]:
        preferred = {product: contract for contract in contracts if (product := self._product_from_contract(contract))}
        result: dict[str, FutureQuote] = {}
        for product, config in PRODUCT_CONFIGS.items():
            try:
                rows = self._realtime_rows(config.future_name)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{product} 逐品种期货实时源获取失败: {self._brief_error(exc)}")
                continue
            row = self._select_main_future_row(product, rows, preferred.get(product))
            if row is None:
                warnings.append(f"{product} 逐品种期货实时源未找到主力合约")
                continue
            contract = self._infer_contract(row) or preferred.get(product) or product
            price = self._first_float(row, ["current_price", "trade", "最新价", "price"])
            if price <= 0:
                warnings.append(f"{product} 逐品种期货实时源价格无效")
                continue
            result[product] = self._future_quote_from_row(now, row, product, contract, price)
        return result

    def _select_main_future_row(
        self,
        product: str,
        rows: list[dict[str, Any]],
        preferred_contract: str | None,
    ) -> dict[str, Any] | None:
        product_rows = [row for row in rows if (self._infer_product(row) or product) == product]
        if preferred_contract:
            for row in product_rows:
                if self._infer_contract(row) == preferred_contract:
                    return row
        for row in product_rows:
            contract = self._infer_contract(row)
            if contract and re.match(rf"^{product}\d{{3,4}}$", contract):
                return row
        return product_rows[0] if product_rows else None

    def _future_quote_from_row(
        self,
        now: datetime,
        row: dict[str, Any],
        product: str,
        contract: str,
        price: float,
    ) -> FutureQuote:
        return FutureQuote(
            product=product,
            contract=contract,
            name=str(row.get("symbol") or row.get("name") or contract),
            price=price,
            change_pct=self._change_pct(row, price),
            volume=self._first_int(row, ["volume", "成交量"]),
            open_interest=self._first_int(row, ["hold", "position", "持仓量"]),
            tick_time=self._parse_tick_time(now, row),
            raw=row,
        )

    def _main_contract_symbols(self, warnings: list[str]) -> list[str]:
        try:
            text = self._call_quiet(self.ak.match_main_contract, symbol="cffex")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"match_main_contract 调用失败: {self._brief_error(exc)}")
            return []

        if isinstance(text, (list, tuple)):
            candidates = [str(item).strip().upper() for item in text]
        else:
            candidates = re.split(r"[,，\s]+", str(text).upper())
        contracts = []
        for item in candidates:
            if re.match(r"^(IF|IH|IC|IM)\d{3,4}$", item):
                contracts.append(item)
        return contracts

    def _fetch_spots(self, now: datetime, warnings: list[str]) -> dict[str, SpotQuote]:
        wanted = {config.spot_code: product for product, config in PRODUCT_CONFIGS.items()}
        result = self._fetch_spots_from_sina(now, wanted, warnings, is_fallback=False)
        missing_products = {product for product in wanted.values() if product not in result}
        if missing_products:
            result.update(self._fetch_spots_from_em(now, wanted, warnings))

        missing = [product for product in wanted.values() if product not in result]
        if missing:
            warnings.append(f"现货指数缺失: {','.join(missing)}")

        return {product: quote for product, quote in result.items() if quote.price > 0}

    def _fetch_spots_from_em(
        self,
        now: datetime,
        wanted: dict[str, str],
        warnings: list[str],
    ) -> dict[str, SpotQuote]:
        categories = ("沪深重要指数", "上证系列指数", "中证系列指数")
        result: dict[str, SpotQuote] = {}
        for category in categories:
            try:
                df = self._call_quiet(self.ak.stock_zh_index_spot_em, symbol=category)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"现货指数 {category} 补充源获取失败: {self._brief_error(exc)}")
                continue
            result.update(self._parse_spot_rows(now, self._rows(df), wanted))
        if result:
            warnings.append("现货指数已使用东方财富补充源")
        return result

    def _fetch_spots_from_sina(
        self,
        now: datetime,
        wanted: dict[str, str],
        warnings: list[str],
        is_fallback: bool = True,
    ) -> dict[str, SpotQuote]:
        try:
            df = self._call_quiet(self.ak.stock_zh_index_spot_sina)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"新浪指数备用源获取失败: {self._brief_error(exc)}")
            return {}

        result = self._parse_spot_rows(now, self._rows(df), wanted)
        if result and is_fallback:
            warnings.append("现货指数已使用新浪备用源")
        return result

    def _parse_spot_rows(
        self,
        now: datetime,
        rows: list[dict[str, Any]],
        wanted: dict[str, str],
    ) -> dict[str, SpotQuote]:
        result: dict[str, SpotQuote] = {}
        for row in rows:
            code = normalize_index_code(row.get("代码"))
            product = wanted.get(code)
            if not product:
                continue
            result[product] = SpotQuote(
                product=product,
                index_code=code,
                name=str(row.get("名称") or PRODUCT_CONFIGS[product].spot_name),
                price=self._first_float(row, ["最新价", "price", "最新"]),
                change_pct=self._first_float(row, ["涨跌幅", "change_pct"]),
                volume=self._first_float(row, ["成交量", "volume"], None),
                amount=self._first_float(row, ["成交额", "amount"], None),
                tick_time=self._parse_spot_tick_time(now, row),
                raw=row,
            )
        return result

    def _fetch_terms_if_due(
        self,
        now: datetime,
        spots: dict[str, SpotQuote],
        warnings: list[str],
    ) -> dict[str, list[TermQuote]]:
        if not self.settings.fetch_term_structure:
            return {}
        if self._last_term_fetch_at is not None:
            age = (now - self._last_term_fetch_at).total_seconds()
            if age < self.settings.fetch_term_structure_every_seconds:
                return self._last_terms

        terms: dict[str, list[TermQuote]] = {}
        for product, config in PRODUCT_CONFIGS.items():
            try:
                rows = self._realtime_rows(config.future_name)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{product} 期限结构获取失败: {self._brief_error(exc)}")
                continue
            product_terms: list[TermQuote] = []
            for row in rows:
                row_product = self._infer_product(row) or product
                if row_product != product:
                    continue
                contract = self._infer_contract(row)
                if not contract or not re.match(rf"^{product}\d{{3,4}}$", contract):
                    continue
                price = self._first_float(row, ["trade", "current_price", "最新价"])
                if price <= 0:
                    continue
                spot = spots.get(product)
                item_basis = basis(price, spot.price) if spot else None
                item_basis_bp = basis_bp(price, spot.price) if spot else None
                product_terms.append(
                    TermQuote(
                        product=product,
                        contract=contract,
                        price=price,
                        basis=item_basis,
                        basis_bp=item_basis_bp,
                        volume=self._first_int(row, ["volume", "成交量"], None),
                        open_interest=self._first_int(row, ["position", "hold", "持仓量"], None),
                        tick_time=self._parse_tick_time(now, row),
                    )
                )
            terms[product] = sorted(product_terms, key=lambda item: contract_sort_key(item.contract))[:4]

        self._last_term_fetch_at = now
        self._last_terms = terms
        return terms

    def _fetch_positions_if_due(self, now: datetime, warnings: list[str]) -> dict[str, PositionRankSignal]:
        if not self.settings.fetch_position_rank:
            return {}
        date_text = now.strftime("%Y%m%d")
        if self._last_position_date == date_text and self._last_positions:
            return self._last_positions
        if self._last_position_empty_at is not None:
            age = (now - self._last_position_empty_at).total_seconds()
            if age < self.settings.position_rank_empty_retry_seconds:
                if self._last_positions:
                    warnings.append("今日持仓排名暂不可用，继续使用上一可用交易日排名")
                    return self._last_positions
                warnings.append("今日持仓排名暂不可用，等待下次重试")
                return {}

        positions = self._fetch_positions_for_date(date_text, now, warnings)
        if positions:
            self._last_position_date = date_text
            self._last_positions = positions
            self._last_position_empty_at = None
            return positions

        self._last_position_empty_at = now
        fallback = self._fetch_previous_available_positions(now, warnings)
        if fallback:
            self._last_positions = fallback
            return fallback
        return {}

    def _fetch_positions_for_date(
        self,
        date_text: str,
        now: datetime,
        warnings: list[str],
    ) -> dict[str, PositionRankSignal]:
        try:
            rank_sum = self._call_quiet(self.ak.get_rank_sum, date=date_text, vars_list=list(PRODUCTS))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"中金所持仓汇总获取失败: {self._brief_error(exc)}")
            return {}

        citic_changes = self._fetch_citic_net_short_changes(date_text, warnings)
        rows = self._rows(rank_sum)
        positions: dict[str, PositionRankSignal] = {}
        for product in PRODUCTS:
            product_rows = [row for row in rows if str(row.get("variety") or row.get("var") or "").upper() == product]
            if not product_rows:
                continue
            long_total = sum(self._first_int(row, ["long_open_interest_top20"], 0) or 0 for row in product_rows)
            short_total = sum(self._first_int(row, ["short_open_interest_top20"], 0) or 0 for row in product_rows)
            long_change = sum(self._first_int(row, ["long_open_interest_chg_top20"], 0) or 0 for row in product_rows)
            short_change = sum(self._first_int(row, ["short_open_interest_chg_top20"], 0) or 0 for row in product_rows)
            positions[product] = PositionRankSignal(
                product=product,
                net_short_top20=short_total - long_total,
                net_short_change_top20=short_change - long_change,
                citic_net_short_change=citic_changes.get(product),
                as_of_date=date_text,
                lag_days=max(0, (now.date() - datetime.strptime(date_text, "%Y%m%d").date()).days),
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
            fallback_warnings: list[str] = []
            positions = self._fetch_positions_for_date(date_text, now, fallback_warnings)
            if positions:
                warnings.append(f"今日持仓排名暂不可用，已使用 {date_text} 排名")
                self._last_position_date = date_text
                return positions
        warnings.append("今日及上一可用交易日持仓排名均不可用，本次持仓排名降为中性")
        return {}

    def _fetch_citic_net_short_changes(self, date_text: str, warnings: list[str]) -> dict[str, int]:
        try:
            rank_table = self._call_quiet(self.ak.get_cffex_rank_table, date=date_text, vars_list=list(PRODUCTS))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"中信期货席位持仓获取失败: {self._brief_error(exc)}")
            return {}
        if not isinstance(rank_table, dict):
            return {}
        result: dict[str, int] = {}
        for contract, df in rank_table.items():
            product = self._product_from_contract(contract)
            if product not in PRODUCT_CONFIGS:
                continue
            long_change = 0
            short_change = 0
            for row in self._rows(df):
                if "中信期货" in str(row.get("long_party_name") or ""):
                    long_change += self._first_int(row, ["long_open_interest_chg"], 0) or 0
                if "中信期货" in str(row.get("short_party_name") or ""):
                    short_change += self._first_int(row, ["short_open_interest_chg"], 0) or 0
            result[product] = result.get(product, 0) + short_change - long_change
        return result

    def _fetch_position_trends_if_due(self, now: datetime, warnings: list[str]) -> dict[str, PositionTrendSignal]:
        if not self.settings.fetch_position_rank or self.settings.position_trend_days <= 1:
            return {}
        date_text = now.strftime("%Y%m%d")
        if self._last_position_trend_date == date_text:
            return self._last_position_trends

        lookback_days = max(self.settings.position_trend_days * 2, 10)
        start_day = (now.date() - timedelta(days=lookback_days)).strftime("%Y%m%d")
        try:
            df = self._call_quiet(
                self.ak.get_rank_sum_daily,
                start_day=start_day,
                end_day=date_text,
                vars_list=list(PRODUCTS),
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"近期期货持仓趋势获取失败: {self._brief_error(exc)}")
            self._last_position_trend_date = date_text
            self._last_position_trends = {}
            return {}

        by_product_date: dict[str, dict[str, int]] = {product: {} for product in PRODUCTS}
        for row in self._rows(df):
            product = str(row.get("variety") or row.get("var") or "").upper()
            if product not in by_product_date:
                continue
            row_date = str(row.get("date") or "")
            if not row_date:
                continue
            long_change = self._first_int(row, ["long_open_interest_chg_top20"], 0) or 0
            short_change = self._first_int(row, ["short_open_interest_chg_top20"], 0) or 0
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
        return trends

    @staticmethod
    def _rows(df: Any) -> list[dict[str, Any]]:
        if df is None:
            return []
        if hasattr(df, "to_dict"):
            return [dict(row) for row in df.to_dict(orient="records")]
        if isinstance(df, list):
            return [dict(row) for row in df if isinstance(row, dict)]
        return []

    @staticmethod
    def _call_quiet(func: Any, *args: Any, **kwargs: Any) -> Any:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            return func(*args, **kwargs)

    @staticmethod
    def _brief_error(exc: Exception, limit: int = 180) -> str:
        name = type(exc).__name__
        text = " ".join(str(exc).split())
        if name == "ProxyError" or "ProxyError" in text:
            return "ProxyError: 代理或网络连接失败"
        if name in {"ReadTimeout", "ConnectTimeout", "Timeout"} or "timeout" in text.lower():
            return f"{name}: 请求超时"
        if name == "ConnectionError":
            return "ConnectionError: 网络连接失败"
        if len(text) > limit:
            text = text[:limit] + "..."
        return f"{name}: {text}"

    @staticmethod
    def _product_from_contract(value: str) -> str | None:
        match = re.match(r"^(IF|IH|IC|IM)", str(value).upper())
        return match.group(1) if match else None

    @classmethod
    def _infer_product(cls, row: dict[str, Any]) -> str | None:
        joined = " ".join(str(value) for value in row.values())
        by_code = re.search(r"\b(IF|IH|IC|IM)(?:\d{3,4}|0|88|888|99)?\b", joined.upper())
        if by_code:
            return by_code.group(1)
        if "沪深300" in joined:
            return "IF"
        if "上证50" in joined:
            return "IH"
        if "中证500" in joined:
            return "IC"
        if "中证1000" in joined:
            return "IM"
        return None

    @staticmethod
    def _infer_contract(row: dict[str, Any]) -> str | None:
        joined = " ".join(str(value) for value in row.values())
        match = re.search(r"\b(IF|IH|IC|IM)(\d{3,4}|0|88|888|99)\b", joined.upper())
        return match.group(0) if match else None

    @staticmethod
    def _first_float(row: dict[str, Any], keys: list[str], default: float | None = 0.0) -> float | None:
        for key in keys:
            if key in row and row[key] is not None:
                return to_float(row[key], default if default is not None else 0.0)
        return default

    @staticmethod
    def _first_int(row: dict[str, Any], keys: list[str], default: int | None = 0) -> int | None:
        for key in keys:
            if key in row and row[key] is not None:
                return to_int(row[key], default if default is not None else 0)
        return default

    @staticmethod
    def _change_pct(row: dict[str, Any], price: float) -> float:
        direct = AkShareDataSource._first_float(row, ["changepercent", "涨跌幅", "change_pct"], None)
        if direct is not None:
            return direct
        previous = AkShareDataSource._first_float(row, ["preclose", "presettlement", "昨收", "昨日结算"], None)
        if previous and previous > 0:
            return (price / previous - 1) * 100
        return 0.0

    def _parse_tick_time(self, now: datetime, row: dict[str, Any]) -> datetime | None:
        date_text = str(row.get("tradedate") or row.get("date") or now.date().isoformat()).strip()
        candidates = [
            row.get("datetime"),
            row.get("更新时间"),
            row.get("ticktime"),
            row.get("time"),
        ]
        for candidate in candidates:
            parsed = self._parse_datetime_value(candidate, date_text)
            if parsed is not None:
                return parsed
        return None

    def _parse_spot_tick_time(self, now: datetime, row: dict[str, Any]) -> datetime | None:
        date_text = str(row.get("日期") or row.get("date") or now.date().isoformat()).strip()
        candidates = [
            row.get("更新时间"),
            row.get("时间"),
            row.get("time"),
            row.get("datetime"),
        ]
        for candidate in candidates:
            parsed = self._parse_datetime_value(candidate, date_text)
            if parsed is not None:
                return parsed
        return None

    def _parse_datetime_value(self, value: Any, date_text: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        full_formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y%m%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y%m%d %H:%M",
        )
        for fmt in full_formats:
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=self.settings.tz)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=self.settings.tz)
            return parsed.astimezone(self.settings.tz)
        except ValueError:
            pass

        time_formats = ("%H:%M:%S", "%H:%M")
        for fmt in time_formats:
            try:
                combined = datetime.strptime(f"{date_text} {text}", f"%Y-%m-%d {fmt}")
            except ValueError:
                try:
                    combined = datetime.strptime(f"{date_text} {text}", f"%Y%m%d {fmt}")
                except ValueError:
                    continue
            return combined.replace(tzinfo=self.settings.tz)
        return None

    def _realtime_rows(self, symbol: str) -> list[dict[str, Any]]:
        if symbol not in self._realtime_cache:
            df = self._call_quiet(self.ak.futures_zh_realtime, symbol=symbol)
            self._realtime_cache[symbol] = self._rows(df)
        return self._realtime_cache[symbol]
