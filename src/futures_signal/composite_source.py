from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Callable

from .akshare_providers import FetchObservation, ProviderObservation, RealtimeQuoteBundle, RealtimeQuoteBundleProvider
from .akshare_utils import (
    brief_error,
    call_quiet,
    first_float,
    first_int,
    infer_contract,
    infer_product,
    product_from_contract,
    rows,
)
from .config import Settings
from .data_sources import DataSourceError
from .market_calendar import TradingCalendar
from .metrics import basis, basis_bp, contract_sort_key, normalize_index_code
from .models import (
    FutureQuote,
    MarketSnapshot,
    PRODUCT_CONFIGS,
    PRODUCTS,
    SpotQuote,
    TermQuote,
)
from .providers import PositionRankProvider, PositionTrendProvider

logger = logging.getLogger(__name__)
_REAL_DATETIME = datetime


class CompositeMarketDataSource:
    def __init__(
        self,
        settings: Settings,
        ak: Any,
        quote_bundle_provider: RealtimeQuoteBundleProvider,
        calendar: TradingCalendar,
        *,
        position_rank_provider: PositionRankProvider | None = None,
        position_trend_provider: PositionTrendProvider | None = None,
    ):
        self.settings = settings
        self.ak = ak
        self.quote_bundle_provider = quote_bundle_provider
        self.calendar = calendar
        self._last_term_fetch_at: datetime | None = None
        self._last_terms: dict[str, list[TermQuote]] = {}
        self.position_rank_provider = position_rank_provider or PositionRankProvider(ak=ak, settings=settings, calendar=calendar)
        self.position_trend_provider = position_trend_provider or PositionTrendProvider(ak=ak, settings=settings)
        self.last_fetch_observation = FetchObservation()

    def fetch(self) -> MarketSnapshot:
        now = datetime.now(self.settings.tz)
        warnings: list[str] = []
        bundle = self.quote_bundle_provider.create()
        observation = FetchObservation()

        futures, main_observation = self._observe_provider(
            "main_futures",
            lambda: self._fetch_main_futures(now, warnings, bundle=bundle),
        )
        observation = observation.add(main_observation)

        spots, spot_observation = self._observe_provider(
            "spots",
            lambda: self._fetch_spots(now, warnings),
        )
        observation = observation.add(spot_observation)

        terms, term_observation = self._observe_provider(
            "terms",
            lambda: self._fetch_terms_if_due(now, spots, warnings, bundle=bundle),
        )
        observation = observation.add(term_observation)

        positions, position_observation = self._observe_existing_observation(
            lambda: self.position_rank_provider.fetch(now, warnings)
        )
        observation = observation.add(position_observation)

        position_trends, trend_observation = self._observe_existing_observation(
            lambda: self.position_trend_provider.fetch(now, warnings)
        )
        observation = observation.add(trend_observation)
        self.last_fetch_observation = observation
        self._log_observation(observation)

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

    def _observe_provider(
        self,
        provider: str,
        fetcher: Callable[[], Any],
    ) -> tuple[Any, ProviderObservation]:
        started = time.perf_counter()
        try:
            result = fetcher()
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            return {}, ProviderObservation.failed(
                provider,
                {"error": type(exc).__name__},
            ).with_duration(duration_ms)
        duration_ms = (time.perf_counter() - started) * 1000
        details = self._provider_details(provider, result)
        return result, self._status_for(provider, result, details).with_duration(duration_ms)

    def _observe_existing_observation(
        self,
        fetcher: Callable[[], tuple[Any, ProviderObservation]],
    ) -> tuple[Any, ProviderObservation]:
        started = time.perf_counter()
        result, observation = fetcher()
        duration_ms = (time.perf_counter() - started) * 1000
        return result, observation.with_duration(duration_ms)

    def _provider_details(self, provider: str, result: Any) -> dict[str, Any]:
        if provider == "main_futures":
            products = sorted(result)
            return {"products": len(products), "missing_products": [item for item in PRODUCTS if item not in result]}
        if provider == "spots":
            products = sorted(result)
            return {"products": len(products), "missing_products": [item for item in PRODUCTS if item not in result]}
        if provider == "terms":
            return {
                "products": sum(1 for product_terms in result.values() if product_terms),
                "cache_hit": self._last_term_fetch_at is not None,
            }
        return {}

    def _status_for(self, provider: str, result: Any, details: dict[str, Any]) -> ProviderObservation:
        if provider in {"main_futures", "spots"}:
            missing = details.get("missing_products") or []
            if missing and result:
                return ProviderObservation.degraded(provider, details)
            if not result:
                return ProviderObservation.failed(provider, details)
            return ProviderObservation.ok(provider, details)
        if provider == "terms":
            return ProviderObservation.skipped(provider, details) if not self.settings.fetch_term_structure else ProviderObservation.ok(provider, details)
        return ProviderObservation.ok(provider, details)

    def _log_observation(self, observation: FetchObservation) -> None:
        for item in observation.observations:
            level = logging.DEBUG if item.status in {"ok", "skipped"} else logging.WARNING
            logger.log(
                level,
                "provider=%s status=%s duration_ms=%.2f details=%s",
                item.provider,
                item.status,
                item.duration_ms,
                item.details,
            )

    def _fetch_main_futures(
        self,
        now: datetime,
        warnings: list[str],
        bundle: RealtimeQuoteBundle | None = None,
    ) -> dict[str, FutureQuote]:
        contracts = self._main_contract_symbols(warnings)
        if not contracts:
            contracts = ["IF0", "IH0", "IC0", "IM0"]
            warnings.append("主力合约列表不可用，已回退到新浪连续合约 IF0/IH0/IC0/IM0")

        result = self._fetch_main_futures_from_realtime(now, contracts, warnings, bundle=bundle)
        if all(product in result for product in PRODUCTS):
            return result

        symbol_text = ",".join(contracts)
        try:
            df = call_quiet(self.ak.futures_zh_spot, symbol=symbol_text, market="FF", adjust="0")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"主力期货新浪批量补充源获取失败: {brief_error(exc)}")
            return result

        for index, row in enumerate(rows(df)):
            product = infer_product(row)
            if product is None and index < len(contracts):
                product = product_from_contract(contracts[index])
            if product not in PRODUCT_CONFIGS:
                continue
            contract = infer_contract(row) or (contracts[index] if index < len(contracts) else product)
            price = first_float(row, ["current_price", "trade", "最新价", "price"])
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
        bundle: RealtimeQuoteBundle | None = None,
    ) -> dict[str, FutureQuote]:
        preferred = {product: contract for contract in contracts if (product := product_from_contract(contract))}
        result: dict[str, FutureQuote] = {}
        for product, config in PRODUCT_CONFIGS.items():
            try:
                realtime_rows = self._realtime_rows(config.future_name, bundle=bundle)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{product} 逐品种期货实时源获取失败: {brief_error(exc)}")
                continue
            row = self._select_main_future_row(product, realtime_rows, preferred.get(product))
            if row is None:
                warnings.append(f"{product} 逐品种期货实时源未找到主力合约")
                continue
            contract = infer_contract(row) or preferred.get(product) or product
            price = first_float(row, ["current_price", "trade", "最新价", "price"])
            if price <= 0:
                warnings.append(f"{product} 逐品种期货实时源价格无效")
                continue
            result[product] = self._future_quote_from_row(now, row, product, contract, price)
        return result

    def _select_main_future_row(
        self,
        product: str,
        realtime_rows: list[dict[str, Any]],
        preferred_contract: str | None,
    ) -> dict[str, Any] | None:
        product_rows = [row for row in realtime_rows if (infer_product(row) or product) == product]
        if preferred_contract:
            for row in product_rows:
                if infer_contract(row) == preferred_contract:
                    return row
        for row in product_rows:
            contract = infer_contract(row)
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
            volume=first_int(row, ["volume", "成交量"]),
            open_interest=first_int(row, ["hold", "position", "持仓量"]),
            tick_time=self._parse_tick_time(now, row),
            raw=row,
        )

    def _main_contract_symbols(self, warnings: list[str]) -> list[str]:
        try:
            text = call_quiet(self.ak.match_main_contract, symbol="cffex")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"match_main_contract 调用失败: {brief_error(exc)}")
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
        if {product for product in wanted.values() if product not in result}:
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
                df = call_quiet(self.ak.stock_zh_index_spot_em, symbol=category)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"现货指数 {category} 补充源获取失败: {brief_error(exc)}")
                continue
            result.update(self._parse_spot_rows(now, rows(df), wanted))
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
            df = call_quiet(self.ak.stock_zh_index_spot_sina)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"新浪指数备用源获取失败: {brief_error(exc)}")
            return {}

        result = self._parse_spot_rows(now, rows(df), wanted)
        if result and is_fallback:
            warnings.append("现货指数已使用新浪备用源")
        return result

    def _parse_spot_rows(
        self,
        now: datetime,
        spot_rows: list[dict[str, Any]],
        wanted: dict[str, str],
    ) -> dict[str, SpotQuote]:
        result: dict[str, SpotQuote] = {}
        for row in spot_rows:
            code = normalize_index_code(row.get("代码"))
            product = wanted.get(code)
            if not product:
                continue
            result[product] = SpotQuote(
                product=product,
                index_code=code,
                name=str(row.get("名称") or PRODUCT_CONFIGS[product].spot_name),
                price=first_float(row, ["最新价", "price", "最新"]),
                change_pct=first_float(row, ["涨跌幅", "change_pct"]),
                volume=first_float(row, ["成交量", "volume"], None),
                amount=first_float(row, ["成交额", "amount"], None),
                tick_time=self._parse_spot_tick_time(now, row),
                raw=row,
            )
        return result

    def _fetch_terms_if_due(
        self,
        now: datetime,
        spots: dict[str, SpotQuote],
        warnings: list[str],
        bundle: RealtimeQuoteBundle | None = None,
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
                realtime_rows = self._realtime_rows(config.future_name, bundle=bundle)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{product} 期限结构获取失败: {brief_error(exc)}")
                continue
            product_terms: list[TermQuote] = []
            for row in realtime_rows:
                row_product = infer_product(row) or product
                if row_product != product:
                    continue
                contract = infer_contract(row)
                if not contract or not re.match(rf"^{product}\d{{3,4}}$", contract):
                    continue
                price = first_float(row, ["trade", "current_price", "最新价"])
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
                        volume=first_int(row, ["volume", "成交量"], None),
                        open_interest=first_int(row, ["position", "hold", "持仓量"], None),
                        tick_time=self._parse_tick_time(now, row),
                    )
                )
            terms[product] = sorted(product_terms, key=lambda item: contract_sort_key(item.contract))[:4]

        self._last_term_fetch_at = now
        self._last_terms = terms
        return terms

    def _parse_tick_time(self, now: datetime, row: dict[str, Any]) -> datetime | None:
        date_text = str(row.get("tradedate") or row.get("date") or now.date().isoformat()).strip()
        candidates = [row.get("datetime"), row.get("更新时间"), row.get("ticktime"), row.get("time")]
        for candidate in candidates:
            parsed = self._parse_datetime_value(candidate, date_text)
            if parsed is not None:
                return parsed
        return None

    def _parse_spot_tick_time(self, now: datetime, row: dict[str, Any]) -> datetime | None:
        date_text = str(row.get("日期") or row.get("date") or now.date().isoformat()).strip()
        candidates = [row.get("更新时间"), row.get("时间"), row.get("time"), row.get("datetime")]
        for candidate in candidates:
            parsed = self._parse_datetime_value(candidate, date_text)
            if parsed is not None:
                return parsed
        return None

    def _parse_datetime_value(self, value: Any, date_text: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        full_formats = ("%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d %H:%M")
        for fmt in full_formats:
            try:
                return _REAL_DATETIME.strptime(text, fmt).replace(tzinfo=self.settings.tz)
            except ValueError:
                continue
        try:
            parsed = _REAL_DATETIME.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=self.settings.tz)
            return parsed.astimezone(self.settings.tz)
        except ValueError:
            pass

        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                combined = _REAL_DATETIME.strptime(f"{date_text} {text}", f"%Y-%m-%d {fmt}")
            except ValueError:
                try:
                    combined = _REAL_DATETIME.strptime(f"{date_text} {text}", f"%Y%m%d {fmt}")
                except ValueError:
                    continue
            return combined.replace(tzinfo=self.settings.tz)
        return None

    def _realtime_rows(
        self,
        symbol: str,
        bundle: RealtimeQuoteBundle | None = None,
    ) -> list[dict[str, Any]]:
        quote_bundle = bundle or self.quote_bundle_provider.create()
        return quote_bundle.rows(symbol)

    @staticmethod
    def _change_pct(row: dict[str, Any], price: float) -> float:
        direct = first_float(row, ["changepercent", "涨跌幅", "change_pct"], None)
        if direct is not None:
            return direct
        previous = first_float(row, ["preclose", "presettlement", "昨收", "昨日结算"], None)
        if previous and previous > 0:
            return (price / previous - 1) * 100
        return 0.0
