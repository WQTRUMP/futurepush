# A股股指期货实时信号推送服务

用 AkShare 每分钟采样 IF/IH/IC/IM 与对应现货指数，结合期货领先残差、基差、持仓、前20会员净空变化、风格共振和尾盘信号，通过企业微信群机器人推送关键结论。

## 快速开始

```bash
cp .env.example .env
# 填写 WECOM_WEBHOOK_URL、DEEPSEEK_API_KEY
chmod 600 .env
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m futures_signal test-wecom
python -m futures_signal once
python -m futures_signal evaluate
```

运行态健康检查请在服务进程存活期间验证；不要把 `python -m futures_signal run` 和 `curl` 写成同一条串行前台命令。可用两个终端，或按下面方式后台启动后再探测：

```bash
source .venv/bin/activate
python -m futures_signal run > /tmp/futures-signal.log 2>&1 &
FS_PID=$!
trap 'kill $FS_PID' EXIT
sleep 3
curl -sS http://127.0.0.1:18080/healthz
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/health
```

## Docker 部署

```bash
cp .env.example .env
# 填写企业微信机器人 Webhook 和 DeepSeek 配置
chmod 600 .env
docker compose up -d --build
docker compose logs -f
curl http://127.0.0.1:18080/healthz
```

健康检查默认说明：

- 裸机/虚拟环境运行 `python -m futures_signal run` 时，会同时启动只读健康检查 HTTP 入口：`http://127.0.0.1:18080/healthz`
- 兼容路径：`/health` 与 `/healthz`
- 响应固定返回 `200 OK` 和基础状态字段：`status`、`service`、`time`、`uptime_seconds`、`worker`、`storage`
- 不返回 `WECOM_WEBHOOK_URL`、`DEEPSEEK_API_KEY`、评分权重、策略细节等敏感信息
- Docker Compose 默认把容器内健康端口绑定到宿主机 `127.0.0.1:18080`，仅供本机巡检，不对公网暴露

可选环境变量：

```env
HEALTHCHECK_ENABLED=true
HEALTHCHECK_HOST=127.0.0.1
HEALTHCHECK_PORT=18080
HEALTHCHECK_PATH=/healthz
```

企业微信机器人 Webhook 形如：

```env
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

安全约束：

- `WECOM_WEBHOOK_URL` 仅允许 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?...`
- `DEEPSEEK_BASE_URL` 默认仅允许 `https://api.deepseek.com`
- 如确需通过内部网关转发 AI 请求，必须显式设置 `ALLOW_CUSTOM_AI_BASE_URL=true`
- 生产环境建议设置 `APP_ENV=production` 或 `LOAD_DOTENV=false`，避免自动信任工作目录 `.env`

服务默认通过 AkShare `tool_trade_date_hist_sina` 获取 A 股交易日历，只在交易日的日盘 `09:30-11:30`、`13:00-15:00` 采样。非交易日不会抓行情，也不会推送实时信号。`once` 在非交易时段默认只展示结果，不写入数据库也不推送；如需调试写库，可使用：

```bash
python -m futures_signal once --save-outside-market
```

在线链路现在只负责登记 `predictions`，到期后补写 `prediction_labels` 需要单独运行评估任务；这避免了采样时把历史回标 SQL 同步绑在主流程里。可通过定时任务调用：

```bash
python -m futures_signal evaluate
python -m futures_signal evaluate --until 2026-06-02T10:30:00+08:00 --limit 200
```

交易日历会缓存到 `data/trade_dates.json`。若 AkShare 日历接口临时失败，服务会优先使用缓存；没有缓存时才退回工作日判断。

## CLI

```bash
python -m futures_signal run
python -m futures_signal once
python -m futures_signal once --save-outside-market
python -m futures_signal once --push
python -m futures_signal evaluate
python -m futures_signal test-wecom
python -m futures_signal calendar
```

查看健康状态：

```bash
curl http://127.0.0.1:18080/healthz
curl http://127.0.0.1:18080/health
```

示例响应：

```json
{
  "status": "ok",
  "service": "futures-signal",
  "time": "2026-06-14T12:00:00+08:00",
  "uptime_seconds": 42,
  "worker": {
    "status": "ok",
    "last_sample_at": "2026-06-14T11:59:00+08:00",
    "last_error_at": null
  },
  "storage": {
    "db_path": "data/market.db",
    "db_exists": true,
    "db_readable": true
  }
}
```

## 评分

总分 0-100，核心不是看期货涨跌，而是看 `期货5分钟收益率 - beta * 现货5分钟收益率` 的领先残差。按交易时段使用不同权重：

- 盘中模型：期货领先残差 30%、基差动量 25%、价格-持仓-成交确认 20%、IF/IH/IC/IM 风格共振 15%、现货指数扩散确认 10%。
- 尾盘/次日模型：尾盘期货领先残差 25%、日内基差结构 20%、OI/龙虎榜确认 20%、风格扩散 15%、现货指数扩散确认 10%、外围风险变量 10%。

当前没有接入海外市场或汇率数据源，`外围风险变量` 暂按中性 50 处理，不编造盘后风险。

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
- `lead_residual_5m_pct > 0`：期货端领先现货定价更乐观
- `lead_residual_5m_pct < 0`：期货端领先压低预期

数据可信度规则：

- 入库前校验行情 tick 时间，默认期货或现货 tick 超过 `MAX_QUOTE_AGE_SECONDS=180` 秒会被剔除。
- 同一品种期货和现货 tick 时间差超过 `MAX_TICK_SYNC_SECONDS=60` 秒，该品种不参与评分。
- 合约切换时，当前合约与 5m/日线参考合约不一致的品种会禁用价格、持仓、基差和领先残差差分。
- OI 和前20净空评分使用 `变化量 / 当前持仓量` 归一化，减少 IF/IH/IC/IM 合约规模差异造成的误判。

强多必须同时满足：领先残差为正、基差扩大且 `basis_zscore > 0`、价格上涨、OI 增加、至少两个核心品种共振、现货指数没有明显背离。强空必须同时满足：领先残差为负、基差收窄或贴水扩大、价格下跌、OI 增加、至少两个核心品种共振走弱。

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

推送优先给灯号、方向、周期、置信度、操作和依据，不再展开表格明细。AI 会根据 IF/IH/IC/IM 期货领先残差、基差变化、日线/日内持仓、前20会员净空和成交变化，输出股票板块/风格的短线涨跌倾向。

持仓排名数据来自 AkShare 中金所接口：

- `get_rank_sum`：前5/10/15/20会员多空持仓汇总，用于计算净空扩大或收敛。
- `get_cffex_rank_table`：会员持仓明细，用于跟踪中信期货等关键席位边际净空变化。

盘中今日排名为空时，服务不会把空结果缓存一整天；默认 15 分钟后重试，并临时使用上一可用交易日排名，消息里会标记数据滞后。

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
ALLOW_CUSTOM_AI_BASE_URL=false
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
组件 领先残差92 基差85 价仓量78 共振100 现货扩散70

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
- 强多组合: 期货领先残差为正 + 基差扩大 + 价仓确认 + IF/IC/IM 共振
- 14:30 后尾盘信号偏多

AI点评
期指相对现货明显转强，IC/IM 深贴水低分位修复，说明小中盘对冲压力在缓解。
持仓同步增加，偏向新多入场，而不是单纯空头回补。
尾盘若现货量能跟随，次日惯性溢价概率提升；若量能不跟，需防止冲高回落。
```

5-7 月分红季会自动降低“绝对贴水”的解释权重，更重视贴水是否继续扩大或快速收窄。交割周附近会标记主力换月窗口，减少把正常展期误判成资金情绪突变。

## 回测闭环

每次有效入库会同步写入 `predictions` 表，并在后续目标时点附近有行情样本后写入 `prediction_labels`：

- 早盘预测：跟踪 `10:30`、`11:30`、当日收盘附近收益。
- 盘中预测：跟踪 `11:30`、当日收盘附近收益。
- 尾盘预测：跟踪下一交易日开盘、`10:30`、收盘附近收益。

标签字段包含平均现货未来收益 bp 和方向命中标记，可用于后续统计命中率、高分组收益、分数分桶收益和 IC。

## 数据与日志

- SQLite：`data/market.db`
- 日志：`logs/futures_signal.log`

运行时会主动收紧权限：

- `data/`、数据库父目录、`logs/` 目录：`0700`
- `data/market.db`、`logs/futures_signal.log`：`0600`

`alerts` 表不再保存完整推送正文，只保留哈希和预览，减少策略文本长期明文落盘。

AkShare 实时接口可能受上游延迟或字段变更影响；服务会在消息里提示缺失数据，并避免用空数据生成误导性信号。
