from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
        if value in {"", "-", "--", "nan", "None"}:
            return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    return int(round(to_float(value, float(default))))


def basis(futures_price: float, spot_price: float) -> float:
    """期-现基差。负值表示期货贴水，正值表示期货升水。"""
    return futures_price - spot_price


def basis_bp(futures_price: float, spot_price: float) -> float:
    if spot_price == 0:
        return 0.0
    return basis(futures_price, spot_price) / spot_price * 10000


def basis_state(basis_value: float) -> str:
    if basis_value > 0:
        return "升水"
    if basis_value < 0:
        return "贴水"
    return "平水"


def basis_change_label(change_bp: float | None) -> str:
    if change_bp is None:
        return "历史不足"
    if change_bp > 0:
        return "贴水收窄/升水扩大"
    if change_bp < 0:
        return "贴水扩大/升水收窄"
    return "基差持平"


def percentile_rank(values: list[float], current: float, min_samples: int = 20) -> float | None:
    clean = [value for value in values if not math.isnan(value)]
    if len(clean) < min_samples:
        return None
    less_or_equal = sum(1 for value in clean if value <= current)
    return less_or_equal / len(clean)


def zscore(values: list[float], current: float, min_samples: int = 20) -> float | None:
    clean = [value for value in values if not math.isnan(value)]
    if len(clean) < min_samples:
        return None
    mean = sum(clean) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean) / len(clean)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (current - mean) / std


def is_dividend_season(now: datetime) -> bool:
    return 5 <= now.month <= 7


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def classify_price_oi(price_change: float | None, oi_change: int | None) -> str:
    if price_change is None or oi_change is None:
        return "历史不足"
    if price_change > 0 and oi_change > 0:
        return "多头主动开仓"
    if price_change > 0 and oi_change < 0:
        return "空头平仓推动"
    if price_change < 0 and oi_change > 0:
        return "空头主动加仓"
    if price_change < 0 and oi_change < 0:
        return "多头止损/减仓"
    return "变化不明显"


def score_band(score: int) -> str:
    if score >= 80:
        return "期现共振偏多"
    if score >= 60:
        return "偏多但不强"
    if score >= 40:
        return "中性震荡"
    if score >= 20:
        return "偏空"
    return "明显空头"


def normalize_index_code(value: Any) -> str:
    raw = str(value).strip()
    if raw.endswith(".SH") or raw.endswith(".SZ"):
        raw = raw[:-3]
    raw = re.sub(r"\D", "", raw)
    return raw.zfill(6)[-6:] if raw else ""


def contract_sort_key(contract: str) -> tuple[int, str]:
    match = re.search(r"([A-Z]{1,2})(\d{3,4}|0|88|888|99)$", contract.upper())
    if not match:
        return (999999, contract)
    suffix = match.group(2)
    if suffix in {"0", "88", "888", "99"}:
        return (999998, contract)
    if len(suffix) == 3:
        suffix = "2" + suffix
    return (int(suffix), contract)
