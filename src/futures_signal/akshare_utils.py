from __future__ import annotations

import re
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any

from .metrics import to_float, to_int


def rows(df: Any) -> list[dict[str, Any]]:
    if df is None:
        return []
    if hasattr(df, "to_dict"):
        return [dict(row) for row in df.to_dict(orient="records")]
    if isinstance(df, list):
        return [dict(row) for row in df if isinstance(row, dict)]
    return []


def call_quiet(func: Any, *args: Any, **kwargs: Any) -> Any:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        return func(*args, **kwargs)


def brief_error(exc: Exception, limit: int = 180) -> str:
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


def product_from_contract(value: str) -> str | None:
    match = re.match(r"^(IF|IH|IC|IM)", str(value).upper())
    return match.group(1) if match else None


def infer_product(row: dict[str, Any]) -> str | None:
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


def infer_contract(row: dict[str, Any]) -> str | None:
    joined = " ".join(str(value) for value in row.values())
    match = re.search(r"\b(IF|IH|IC|IM)(\d{3,4}|0|88|888|99)\b", joined.upper())
    return match.group(0) if match else None


def first_float(row: dict[str, Any], keys: list[str], default: float | None = 0.0) -> float | None:
    for key in keys:
        if key in row and row[key] is not None:
            return to_float(row[key], default if default is not None else 0.0)
    return default


def first_int(row: dict[str, Any], keys: list[str], default: int | None = 0) -> int | None:
    for key in keys:
        if key in row and row[key] is not None:
            return to_int(row[key], default if default is not None else 0)
    return default
