# A股股指期货实时信号推送服务

用 AkShare 每分钟采样 IF/IH/IC/IM 与对应现货指数，结合基差、持仓、前20会员净空变化、期现强弱和尾盘信号，通过企业微信群机器人推送关键结论。

## 快速开始

```bash
cp .env.example .env
# 填写 WECOM_WEBHOOK_URL、DEEPSEEK_API_KEY
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m futures_signal test-wecom
python -m futures_signal once
python -m futures_signal run
```

## Docker 部署

```bash
cp .env.example .env
# 填写企业微信机器人 Webhook 和 DeepSeek 配置
docker compose up -d --build
docker compose logs -f
```

企业微信机器人 Webhook 形如：

```env
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

服务默认通过 AkShare `tool_trade_date_hist_sina` 获取 A 股交易日历，只在交易日的日盘 `09:30-11:30`、`13:00-15:00` 采样。非交易日不会抓行情，也不会推送实时信号。若要调试或非交易时段运行，设置：

```env
RUN_OUTSIDE_MARKET_HOURS=true
```

交易日历会缓存到 `data/trade_dates.json`。若 AkShare 日历接口临时失败，服务会优先使用缓存；没有缓存时才退回工作日判断。

## CLI

```bash
python -m futures_signal run
python -m futures_signal once
python -m futures_signal once --push
python -m futures_signal test-wecom
python -m futures_signal calendar
```

## 评分

总分 0-100：

- 基差变化：25%
- 持仓量变化：20%
- 前20会员净空变化：20%
- 期货相对现货强弱：15%
- IF/IC/IM 共振程度：10%
- 14:30 后尾盘变化：10%

档位：

- `80-100`：期现共振偏多
- `60-79`：偏多但不强
- `40-59`：中性震荡
- `20-39`：偏空
- `0-19`：明显空头

内部统一使用 `期货价格 - 现货指数` 作为“期-现基差”。因此：

- 负值：期货贴水
- 正值：期货升水
- `Δ5m > 0`：贴水收窄或升水扩大，偏正面
- `Δ5m < 0`：贴水扩大或升水收窄，偏负面

默认每分钟采样入库，但推送采用日线/当日走势策略：只在关键决策窗口推送当日预测，避免分钟级噪声频繁打扰。

```env
PUSH_POLICY=daily
DAILY_PUSH_TIMES=09:35,10:30,14:30
DAILY_PUSH_WINDOW_SECONDS=600
DAILY_ALERT_COOLDOWN_SECONDS=82800
URGENT_ALERT_COOLDOWN_SECONDS=3600
```

默认含义：

- `09:35`：开盘后用期货/现货相对强弱给出当日初判
- `10:30`：上午趋势确认，过滤开盘噪声
- `14:30`：尾盘判断，额外加入近几日前20会员净空趋势，偏次日和持仓风险参考
- 非窗口期间只保留强多、强空、总分极端偏多/偏空这类高门槛提醒

如需恢复旧的分钟事件推送，设置：

```env
PUSH_POLICY=event
```

## AI 点评

推送优先给灯号、方向、操作和依据，不再展开表格明细。AI 会根据 IF/IH/IC/IM 期货相对现货强弱、基差变化、日线/日内持仓、前20会员净空和成交变化，输出股票板块/风格的短线涨跌倾向。

持仓排名数据来自 AkShare 中金所接口：

- `get_rank_sum`：前5/10/15/20会员多空持仓汇总，用于计算净空扩大或收敛。
- `get_cffex_rank_table`：会员持仓明细，用于跟踪中信期货等关键席位边际净空变化。

板块映射：

- IH：大金融、银行、保险、券商、央国企红利
- IF：沪深300权重、核心资产、消费和新能源龙头
- IC：中盘制造、周期、医药、TMT中盘
- IM：小盘成长、题材、高弹性方向

默认配置：

```env
AI_COMMENTARY_ENABLED=true
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_MAX_TOKENS=420
DEEPSEEK_THINKING_ENABLED=false
```

`deepseek-v4-pro` 是默认最新模型配置；如需降低成本或延迟，可改为 `deepseek-v4-flash`。点评失败不会阻断企业微信推送，消息会继续发送客观行情部分。

测试 AI 点评：

```bash
python -m futures_signal test-ai
```

## 推送样式示例

```text
A股股指期货信号 2026-05-27 14:42:00
总分 82/100 | 期现共振偏多
上次 58/100 | 中性震荡
组件 基差92 持仓85 期现78 共振100 尾盘90

品种 合约 期货%/现货% 期-现bp 状态 Δ5m 分位 持仓Δ 成交Δ 组合
IF IF2606 +0.42%/+0.25% -12.6 贴水 +4.8 28% +1840 +16520 多头主动开仓
IH IH2606 +0.31%/+0.20% -8.1 贴水 +2.7 36% +620 +5520 多头主动开仓
IC IC2606 +0.58%/+0.33% -38.4 贴水 +7.6 9% +2920 +21800 多头主动开仓
IM IM2606 +0.71%/+0.39% -55.2 贴水 +10.3 6% +4860 +34210 多头主动开仓

期限结构
IF IF2606:-12.6bp(贴水) IF2607:-18.4bp(贴水) IF2609:-32.1bp(贴水)
IC IC2606:-38.4bp(贴水) IC2607:-46.8bp(贴水) IC2609:-73.5bp(贴水)
IM IM2606:-55.2bp(贴水) IM2607:-68.9bp(贴水) IM2609:-102.4bp(贴水)

触发
- 评分档位变化: 中性震荡 -> 期现共振偏多
- 深贴水低分位后快速收敛: IC,IM
- 强多组合: 上涨 + 增仓 + 基差改善 + IF/IC/IM 共振
- 14:30 后尾盘信号偏多

AI点评
期指相对现货明显转强，IC/IM 深贴水低分位修复，说明小中盘对冲压力在缓解。
持仓同步增加，偏向新多入场，而不是单纯空头回补。
尾盘若现货量能跟随，次日惯性溢价概率提升；若量能不跟，需防止冲高回落。
```

5-7 月分红季会自动降低“绝对贴水”的解释权重，更重视贴水是否继续扩大或快速收窄。交割周附近会标记主力换月窗口，减少把正常展期误判成资金情绪突变。

## 数据与日志

- SQLite：`data/market.db`
- 日志：`logs/futures_signal.log`

AkShare 实时接口可能受上游延迟或字段变更影响；服务会在消息里提示缺失数据，并避免用空数据生成误导性信号。
