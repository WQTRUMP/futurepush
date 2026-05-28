from __future__ import annotations

import json

import requests

from .config import Settings
from .models import MarketAnalysis, ProductSignal


class AICommentaryError(RuntimeError):
    pass


class AICommentaryClient:
    def __init__(self, settings: Settings):
        self.enabled = settings.ai_commentary_enabled
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url.rstrip("/")
        self.model = settings.deepseek_model
        self.timeout_seconds = settings.deepseek_timeout_seconds
        self.max_tokens = settings.deepseek_max_tokens
        self.temperature = settings.deepseek_temperature
        self.thinking_enabled = settings.deepseek_thinking_enabled
        self.reasoning_effort = settings.deepseek_reasoning_effort

    def generate(self, analysis: MarketAnalysis) -> str | None:
        if not self.enabled:
            return None
        if not self.api_key:
            return "AI点评暂不可用：未配置 DEEPSEEK_API_KEY。"

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是A股股指期货和股票板块联动盯盘助手。基于用户提供的结构化数据做简洁点评，"
                        "必须根据IF、IH、IC、IM期货与对应现货指数的相对强弱、基差变化、持仓和成交变化，"
                        "推断股票板块/风格的短线涨跌倾向。"
                        "映射规则：IH偏大金融、银行、保险、券商、央国企红利；IF偏沪深300权重和核心资产；"
                        "IC偏中盘制造、周期、医药和TMT中盘；IM偏小盘成长、题材和高弹性方向。"
                        "输出中文，手机阅读格式，最多7行。"
                        "固定包含：1行总判断；2到4行板块倾向，用“偏强/中性/偏弱”表达；1行风险。"
                        "不要编造未提供的行业实时涨跌，不给确定性收益承诺。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(_analysis_for_prompt(analysis), ensure_ascii=False),
                },
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "thinking": {"type": "enabled" if self.thinking_enabled else "disabled"},
            "stream": False,
        }
        if self.thinking_enabled:
            payload["reasoning_effort"] = self.reasoning_effort
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise AICommentaryError(f"DeepSeek 请求失败: {type(exc).__name__}") from exc

        if not response.ok:
            raise AICommentaryError(f"DeepSeek 返回 HTTP {response.status_code}: {response.text[:200]}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AICommentaryError("DeepSeek 响应格式异常") from exc

        return _clean_commentary(str(content))


def _analysis_for_prompt(analysis: MarketAnalysis) -> dict[str, object]:
    return {
        "timestamp": analysis.timestamp.isoformat(timespec="seconds"),
        "score": analysis.score,
        "band": analysis.band,
        "previous_score": analysis.previous_score,
        "previous_band": analysis.previous_band,
        "components": analysis.components,
        "signals": {product: _signal_for_prompt(signal) for product, signal in analysis.signals.items()},
        "reasons": analysis.reasons,
        "warnings": analysis.warnings[:3],
        "term_summary": analysis.term_summary,
        "basis_definition": "期-现基差=期货价格-现货指数；负值为贴水，正值为升水；Δ5m>0表示贴水收窄或升水扩大。",
        "sector_mapping": {
            "IH": "大金融、银行、保险、券商、央国企红利",
            "IF": "沪深300权重、核心资产、消费和新能源龙头",
            "IC": "中盘制造、周期、医药、TMT中盘",
            "IM": "小盘成长、题材、高弹性方向",
        },
        "required_output": "先给总判断，再给板块倾向，最后给风险；板块只给倾向，不声称真实行业涨跌。",
    }


def _signal_for_prompt(signal: ProductSignal) -> dict[str, object]:
    return {
        "contract": signal.contract,
        "futures_change_pct": round(signal.futures_change_pct, 3),
        "spot_change_pct": round(signal.spot_change_pct, 3),
        "basis_bp": round(signal.basis_bp, 2),
        "basis_state": signal.basis_state,
        "basis_change_5m_bp": None if signal.basis_change_bp is None else round(signal.basis_change_bp, 2),
        "basis_percentile": None if signal.basis_percentile is None else round(signal.basis_percentile, 3),
        "futures_minus_spot_pct": round(signal.futures_minus_spot_pct, 3),
        "open_interest_change": signal.open_interest_change,
        "volume_change": signal.volume_change,
        "price_oi_signal": signal.price_oi_signal,
    }


def _clean_commentary(content: str, max_chars: int = 700) -> str:
    lines = [line.strip(" \t\r\n-•") for line in content.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "..."
    return cleaned
