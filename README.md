# A股股指期货实时信号推送服务

用 AkShare 每分钟采样 IF/IH/IC/IM 与对应现货指数，计算基差、持仓、成交、期现强弱、共振和尾盘信号，通过企业微信群机器人推送关键变化。

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

- 基差变化：30%
- 持仓量变化：25%
- 期货相对现货强弱：20%
- IF/IC/IM 共振程度：15%
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

默认每分钟采样入库，但只在跨档、强多/强空、深贴水低分位修复、主力合约切换、贴水快速扩大或配置为 `PUSH_EVERY_SAMPLE=true` 时推送。

## AI 点评

推送会先包含客观数据，再追加 DeepSeek 生成的“AI点评”。默认配置：

```env
AI_COMMENTARY_ENABLED=true
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
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
