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
                        "你是A股T+1交易规则下的股指期货日内/日线级别走势预测助手。"
                        "基于用户提供的结构化数据做当日走势判断，"
                        "必须根据IF、IH、IC、IM期货与对应现货指数的相对强弱、基差变化、日线持仓变化、日内持仓变化、成交变化和前20会员净空变化，"
                        "推断A股当日指数方向、可交易节奏和股票板块/风格的涨跌倾向。"
                        "映射规则：IH偏大金融、银行、保险、券商、央国企红利；IF偏沪深300权重和核心资产；"
                        "IC偏中证500、中盘制造、周期、医药和TMT中盘；IM偏中证1000、小盘成长、高弹性小票。"
                        "输出中文，手机阅读格式，最多7行。"
                        "固定包含：1行当日走势预测；1行交易节奏；1到2行板块取舍；1行风险。"
                        "不要在输出中列出具体期货/现货数值、合约号、基差bp、持仓或成交数值。"
                        "前20会员净空变化和日线持仓变化优先级高于5分钟持仓变化；若两者冲突，需要指出短线噪声或日线资金结构未确认。"
                        "必须识别期货市场常见反向套路：增仓不一定看涨，若期货弱于现货或基差走坏，"
                        "按套保/对冲空单或空头加仓处理；上涨减仓按空头回补处理；"
                        "IF/IH强而IC/IM弱按权重护盘、小票风险偏好不足处理；"
                        "IC/IM强而IF/IH弱按中小盘风格走强、指数级别持续性打折处理；若IM净空扩大，则优先按小盘成长承压处理。"
                        "这些套路必须由你内部消化为明确结论，不要把判断过程或含糊措辞输出给用户。"
                        "输出要直接给方向、仓位节奏、板块取舍和风险等级。"
                        "强调这是日线/当日级别判断，不围绕1分钟噪声下结论。"
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
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise AICommentaryError(f"DeepSeek 请求失败: {type(exc).__name__}") from exc

        if not response.ok:
            raise AICommentaryError(f"DeepSeek 返回 HTTP {response.status_code}")

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
        "position_trends": {
            product: {
                "days": trend.days,
                "net_short_change_sum": trend.net_short_change_sum,
                "latest_net_short_change": trend.latest_net_short_change,
            }
            for product, trend in analysis.position_trends.items()
        },
        "reasons": analysis.reasons,
        "warnings": analysis.warnings[:3],
        "term_summary": analysis.term_summary,
        "basis_definition": "期-现基差=期货价格-现货指数；负值为贴水，正值为升水；Δ5m>0表示贴水收窄或升水扩大。",
        "lead_residual_definition": "lead_residual_5m_pct=期货5分钟收益率 - beta * 现货5分钟收益率；>0表示期货端领先定价更乐观，<0表示期货端领先压低预期。",
        "daily_signal_definition": "daily_* 字段表示当前样本相对上一交易日最后可用快照的日线级别变化，用于T+1判断；5m字段只作短线确认。",
        "position_rank_definition": "net_short_change_top20=前20会员空单变化-多单变化；>0表示净空扩大，<0表示净空收敛。*_ratio字段为变化量/当前持仓量。position_rank_is_fallback=true表示使用上一可用交易日排名。",
        "sector_mapping": {
            "IH": "大金融、银行、保险、券商、央国企红利",
            "IF": "沪深300权重、核心资产、消费和新能源龙头",
            "IC": "中证500、中盘制造、周期、医药、TMT中盘",
            "IM": "中证1000、小盘成长、高弹性小票",
        },
        "required_output": "先给当日走势预测，再给交易节奏建议，再给板块取舍，最后给风险等级；输出明确结论，不把套路识别过程交给用户二次判断；不输出具体行情数值。",
        "trap_detection": [
            "增仓但期货弱于现货或基差走坏：优先按套保/对冲空单或空头加仓处理，不直接看多。",
            "指数跌、持仓增、净空扩大、贴水扩大：按新空头进场处理，现货降风险。",
            "指数涨、持仓降、净空收敛：按空头回补反弹处理，不追高。",
            "指数涨、持仓增、净空收敛：按多头主动进攻处理，可提高风险偏好。",
            "指数跌、持仓降、净空变化小：按多头撤退处理，不急于抄底。",
            "日线增仓但日线基差走坏：按套保/对冲或空头主动增仓处理。",
            "5分钟增仓但日线持仓未确认：只作短线噪声，不上升为日线结论。",
            "上涨但减仓：按空头回补处理，不等于主动做多。",
            "IF/IH强、IC/IM弱：按权重护盘、小票风险偏好不足处理。",
            "IC/IM强、IF/IH弱：按中小盘风格走强、指数级别持续性打折处理；若IM净空扩大，则优先按小盘成长承压处理。",
        ],
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
        "lead_beta": signal.lead_beta,
        "futures_return_5m_pct": None
        if signal.futures_return_5m_pct is None
        else round(signal.futures_return_5m_pct, 4),
        "spot_return_5m_pct": None if signal.spot_return_5m_pct is None else round(signal.spot_return_5m_pct, 4),
        "lead_residual_5m_pct": None
        if signal.lead_residual_5m_pct is None
        else round(signal.lead_residual_5m_pct, 4),
        "open_interest_change": signal.open_interest_change,
        "open_interest_change_ratio": None
        if signal.open_interest_change_ratio is None
        else round(signal.open_interest_change_ratio, 5),
        "volume_change": signal.volume_change,
        "volume_change_ratio": None if signal.volume_change_ratio is None else round(signal.volume_change_ratio, 5),
        "daily_price_change": None if signal.daily_price_change is None else round(signal.daily_price_change, 2),
        "daily_open_interest_change": signal.daily_open_interest_change,
        "daily_open_interest_change_ratio": None
        if signal.daily_open_interest_change_ratio is None
        else round(signal.daily_open_interest_change_ratio, 5),
        "daily_basis_change_bp": None if signal.daily_basis_change_bp is None else round(signal.daily_basis_change_bp, 2),
        "net_short_change_top20": signal.net_short_change_top20,
        "net_short_change_top20_ratio": None
        if signal.net_short_change_top20_ratio is None
        else round(signal.net_short_change_top20_ratio, 5),
        "citic_net_short_change": signal.citic_net_short_change,
        "citic_net_short_change_ratio": None
        if signal.citic_net_short_change_ratio is None
        else round(signal.citic_net_short_change_ratio, 5),
        "position_rank_lag_days": signal.position_rank_lag_days,
        "position_rank_is_fallback": signal.position_rank_is_fallback,
        "price_oi_signal": signal.price_oi_signal,
    }


def _clean_commentary(content: str, max_chars: int = 700) -> str:
    lines = [line.strip(" \t\r\n-•") for line in content.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "..."
    return cleaned
