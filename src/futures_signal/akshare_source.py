from __future__ import annotations

import logging
import re
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from typing import Any

from .config import Settings
from .data_sources import DataSourceError
from .metrics import basis, basis_bp, contract_sort_key, normalize_index_code, to_float, to_int
from .models import FutureQuote, MarketSnapshot, PRODUCT_CONFIGS, PRODUCTS, SpotQuote, TermQuote

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

    def fetch(self) -> MarketSnapshot:
        now = datetime.now(self.settings.tz)
        warnings: list[str] = []
        futures = self._fetch_main_futures(now, warnings)
        spots = self._fetch_spots(now, warnings)
        terms = self._fetch_terms_if_due(now, spots, warnings)

        if not futures:
            raise DataSourceError("AkShare 未返回 IF/IH/IC/IM 期货主力行情")
        if not spots:
            raise DataSourceError("AkShare 未返回对应现货指数行情")

        missing = [product for product in PRODUCTS if product not in futures or product not in spots]
        if missing:
            warnings.append(f"部分品种缺失: {','.join(missing)}")

        return MarketSnapshot(timestamp=now, futures=futures, spots=spots, terms=terms, warnings=warnings)

    def _fetch_main_futures(self, now: datetime, warnings: list[str]) -> dict[str, FutureQuote]:
        contracts = self._main_contract_symbols(warnings)
        if not contracts:
            contracts = ["IF0", "IH0", "IC0", "IM0"]
            warnings.append("主力合约列表不可用，已回退到新浪连续合约 IF0/IH0/IC0/IM0")

        symbol_text = ",".join(contracts)
        try:
            df = self._call_quiet(self.ak.futures_zh_spot, symbol=symbol_text, market="FF", adjust="0")
        except Exception as exc:  # noqa: BLE001 - third-party API can raise many exception types
            raise DataSourceError(f"AkShare futures_zh_spot 调用失败: {self._brief_error(exc)}") from exc

        rows = self._rows(df)
        result: dict[str, FutureQuote] = {}
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
            result[product] = FutureQuote(
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
        return result

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
                tick_time=now,
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
                df = self._call_quiet(self.ak.futures_zh_realtime, symbol=config.future_name)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{product} 期限结构获取失败: {self._brief_error(exc)}")
                continue
            product_terms: list[TermQuote] = []
            for row in self._rows(df):
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
        time_text = str(row.get("ticktime") or row.get("time") or "").strip()
        if not time_text:
            return now
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d %H:%M"):
            try:
                return datetime.strptime(f"{date_text} {time_text}", fmt).replace(tzinfo=self.settings.tz)
            except ValueError:
                continue
        return now
