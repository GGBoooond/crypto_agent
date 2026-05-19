# 🤖 Crypto Agent - 智能加密货币量化交易机器人

一个基于 AI Agent 架构的智能加密货币量化交易系统，采用模块化设计，集成多种 AI 分析能力，提供实时可视化监控，并具备完善的风控机制。

## ✨ 特性

- **Agent 架构**: 采用事件驱动的多 Agent 协作模式
- **多策略融合**: 支持 AI 分析、技术指标、趋势跟踪等多种策略
- **AI 分析**: 集成 DeepSeek 大模型进行市场分析
- **风控系统**: 完善的止损止盈、仓位控制和风险评估
- **实时监控**: 基于 WebSocket 的实时数据推送和可视化面板
- **可扩展**: 易于添加新策略、新交易所、新 Agent

## 📁 项目结构

```
crypto_agent/
├── main.py                   # 主入口
├── config/                   # 配置模块
│   ├── settings.py          # 全局配置
│   └── trading_config.py    # 交易参数
├── core/                     # 核心模块
│   ├── base_agent.py        # Agent 基类
│   ├── orchestrator.py      # 协调器
│   ├── state_store.py       # 状态存储
│   ├── message.py           # 消息定义
│   └── state/               # 拆分状态组件
│       ├── market_state.py  # 行情状态
│       ├── position_state.py # 持仓状态 (SQLite)
│       └── stats.py         # 统计状态
├── agents/                   # Agent 实现
│   ├── market_agent.py      # 行情 Agent
│   ├── strategy_agent.py    # 策略 Agent
│   ├── risk_agent.py        # 风控 Agent
│   ├── executor_agent.py    # 执行 Agent
│   └── logger_agent.py      # 日志 Agent
├── strategies/                    # 策略模块
│   ├── base_strategy.py          # 传统策略基类
│   ├── base_ai_strategy.py       # AI 策略模板基类（统一 prompt/token/验证/skill_used 写回）
│   ├── prompt_only_ai_strategy.py # 纯 prompt 驱动策略基类（声明式 TRIGGER_RULES）
│   ├── llm_client.py             # 通用 LLM 调用封装（OpenAI 协议兼容）
│   ├── ai_strategy.py            # AI 分析策略
│   ├── ai_scalping_strategy.py   # AI 剥头皮策略
│   ├── ai_hybrid_strategy.py     # AI 混合策略 V3
│   ├── ai_hybrid_v4_strategy.py  # AI 混合策略 V4
│   ├── ai_trend_sniper_strategy.py # AI 趋势狙击策略
│   ├── technical_strategy.py     # 技术指标策略
│   └── trend_strategy.py         # 趋势跟踪策略
├── harness/                  # Harness V2 运行时护栏
│   ├── verification/        # 信号校验 (schema/sanity/policy)
│   ├── cost/                # LLM 预算与降级
│   ├── context/             # K线摘要、regime 标签、prompt 构建、StrategyContext 容器
│   ├── observability/       # 全链路追踪 (SQLite+FTS5) + 评估 + forward return 回填
│   ├── hitl/                # 人工审批门 (Telegram 桩)
│   ├── lifecycle/           # 健康监控、checkpoint
│   └── backtest/            # 回测引擎 MVP（CLI / 数据拉取 / 撮合 / 报告）
├── evolution/                # 自演进引擎
│   ├── attribution.py       # skill × regime 离线归因（14 字段 + 混淆矩阵）
│   ├── cli.py               # evolution 命令行入口
│   ├── scheduler.py         # 盘后归因 / 生命周期 / 复盘调度器
│   ├── postmortem.py        # 9 类复盘归因与草案路由
│   ├── skill_health.py      # 技能健康度统计
│   ├── skill_lifecycle.py   # 技能生命周期管理 (QUARANTINE/ACTIVE/PATCHED/SUNSET/DISCARDED)
│   ├── walk_forward.py      # 前向验证
│   └── judge.py             # LLM-as-judge 双签评分
├── memory/                   # 记忆与技能
│   ├── MEMORY.md            # 市场经验记忆
│   ├── USER.md              # 用户偏好
│   ├── memory_tool.py       # 记忆读写工具
│   ├── skill_manage.py      # 技能管理
│   └── skills/              # 技能模板 (SKILL.md)
├── exchange/                 # 交易所适配
│   ├── base_exchange.py     # 交易所基类
│   ├── okx_exchange.py      # OKX 实现
│   └── okx_client_pool.py   # OKX 客户端池
├── indicators/               # 技术指标
│   └── technical.py         # RSI, MACD, BB 等
├── risk/                     # 风控模块 (兼容层 → harness/verification/policy_gate)
│   └── risk_manager.py
├── web/                      # Web 监控 + 回测可视化
│   ├── app.py               # FastAPI 应用 (含路由和 WebSocket)
│   ├── api/                 # 回测 REST/WS 路由
│   ├── frontend/            # Vue3 + Vite 回测前端工程
│   └── static/              # 静态产物 (含 / 与 /backtest)
└── utils/                    # 工具模块
    ├── logger.py            # 日志配置
    └── helpers.py           # 辅助函数
```

## 🚀 快速开始

### 1. 安装依赖

```bash
cd crypto_agent
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API 密钥：

```env
# OKX 交易所
OKX_API_KEY=your_api_key
OKX_SECRET_KEY=your_secret_key
OKX_PASSPHRASE=your_passphrase

# DeepSeek AI
DEEPSEEK_API_KEY=your_deepseek_api_key

# Reviewer LLM / LLM-as-Judge（必须和策略 LLM 不同）
POSTMORTEM_REVIEWER_PROVIDER=qwen
POSTMORTEM_REVIEWER_API_KEY=your_reviewer_key
POSTMORTEM_REVIEWER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
POSTMORTEM_REVIEWER_MODEL=qwen-plus

# 交易配置
TRADING_SYMBOL=DOGE/USDT:USDT
TRADING_AMOUNT=100
TRADING_LEVERAGE=5
TRADING_TIMEFRAME=1m
TRADING_INTERVAL=120
TEST_MODE=true  # 先用测试模式
```

### 3. 启动系统

```bash
python main.py
```

### 4. 访问监控面板

打开浏览器访问: http://localhost:8888

## 🧪 回测功能使用（MVP）

### 快速命令

```bash
python -m harness.backtest \
  --strategy ai_hybrid_v4 \
  --start 2026-04-01 \
  --end 2026-04-30 \
  --symbol DOGE/USDT:USDT \
  --llm-mode replay \
  --initial-balance 10000 \
  --output-dir reports/
```

### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--strategy` | 是 | 策略名，如 `ai_hybrid_v4` |
| `--start` / `--end` | 是 | 回测起止日期（ISO 格式） |
| `--symbol` | 是 | 交易对，如 `DOGE/USDT:USDT` |
| `--llm-mode` | 否 | `replay`（回放历史）或 `rerun`（真实调用 LLM） |
| `--initial-balance` | 否 | 初始资金（USDT） |
| `--timeframe` | 否 | K 线周期，默认 `1m` |
| `--output-dir` | 否 | 报告输出目录，默认 `reports` |
| `--fidelity-check` | 否 | 开启后对比实盘窗口偏差，超阈值返回失败码 |

### 输出结果

- `backtest_<run_id>.json`：完整回测指标（收益、回撤、Sharpe 等）
- `backtest_<run_id>_trades.csv`：逐笔交易记录

### 使用建议

- 先用 `--llm-mode replay` 做基线复盘，再切 `rerun` 观察当前模型漂移
- 实盘前至少做 30 天窗口回测，并关注最大回撤与 profit factor
- 首次使用请确认 `.env` 中仍为 `TEST_MODE=true`

## 🧬 自演进离线任务

离线归因会把 trace 聚合成 `skill_performance` 表，字段包括 `avg_fwd_5m/30m/4h`、最大回撤、简化 Sharpe、IC 与最近样本时间；混淆矩阵会写入 `skill_regime_accuracy`。

```bash
python -m evolution.cli attribution --since 2026-04-01 --window 30m
python -m evolution.cli attribution --confusion-matrix
python -m evolution.cli postmortem --dry-run --limit 200
```

自动调度默认关闭。要让主程序每天跑归因、生命周期和复盘，在 `.env` 里设置 `EVOLUTION_SCHEDULER_ENABLED=true`；reviewer LLM 如需真实调用，配置 `POSTMORTEM_REVIEWER_PROVIDER/API_KEY/MODEL`，且不能和策略 LLM 是同一个 provider+model。

## 🖥️ Backtest 可视化界面

### 访问入口

- 实时监控：`http://localhost:8888/`
- 回测中心：`http://localhost:8888/backtest`

### 回测中心能力

- 可视化创建回测任务（策略、时间区间、symbol、LLM 模式、初始资金、timeframe）
- 实时进度跟踪（WebSocket 推送进度、步数、ETA）
- 回测报告总览列表（状态、胜率、Sharpe、最大回撤）
- 详情页查看（KPI 卡片、资金曲线+回撤、成交明细、原始 JSON）
- 一键下载 `trades.csv` 与删除历史回测

### 前端开发与构建

```bash
# 1) 启动后端
python main.py

# 2) 新开终端，启动前端开发模式
cd web/frontend
npm install
npm run dev
```

Vite 已配置代理到 `http://127.0.0.1:8888`，浏览器访问 `http://localhost:5173/backtest/` 即可联调。

生产构建：

```bash
cd web/frontend
npm run build
```

构建产物会输出到 `web/static/backtest/`，由 FastAPI 在 `/backtest` 路径托管。

## 🏗️ 架构说明

### Agent 协作流程

```
MarketAgent (获取行情)
     │
     ▼ 市场数据
StrategyAgent (多策略分析)
     │
     ▼ 交易信号
RiskAgent (风险评估)
     │
     ▼ 通过/拒绝/修改
ExecutorAgent (执行交易)
     │
     ▼ 订单结果
LoggerAgent (记录日志)
```

### Harness V2 信号链路

信号从生成到执行需经过多层护栏（`harness/`）：

```
行情 → 预算检查 → 策略分析(或降级) → Schema校验 → Sanity校验 → Policy门
  → RiskAgent → HITL审批门 → ExecutorAgent
```

- **预算超限**时自动降级到纯技术指标策略，不消耗 LLM token
- **三层校验**（Schema / Sanity / Policy）拦截格式错误、方向矛盾、仓位超限等异常信号
- 全链路写入 **trace**（SQLite + FTS5），支持事后检索与复盘

### 信号融合机制

支持两种策略模式，由 `STRATEGY_MODE` 配置：

- **single**（默认）：使用单一策略，通过 `ENABLED_STRATEGIES` 指定
- **voting**：加权投票融合多个策略

`strategies/__init__.py` 的 `DEFAULT_WEIGHTS` 给出默认权重（单策略时通常为 1.0，传统经典三件套用 0.4 / 0.3 / 0.3 的分布）。运行时可在 `STRATEGY_REGISTRY` 中注册新策略并按需覆盖权重。

### 风控规则

| 规则 | 默认值 | 说明 |
|------|--------|------|
| 最大持仓比例 | 30% | 单币种最大持仓 |
| 单笔止损 | 2% | 单笔最大亏损 |
| 日止损 | 5% | 单日最大亏损 |
| 最大杠杆 | 10x | 允许的最大杠杆 |
| 连续亏损暂停 | 5次 | 自动暂停交易 |
| 每日最大交易 | 50次 | 单日交易上限 |

## 🛠️ 扩展开发

### 添加新策略

```python
# strategies/my_strategy.py
from strategies.base_strategy import BaseStrategy
from core.message import Signal, SignalType, Confidence

class MyStrategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="MyStrategy", weight=0.3)
    
    async def analyze(self, symbol, klines, market_data, position=None):
        # 实现你的分析逻辑
        return Signal(
            signal_type=SignalType.BUY,
            symbol=symbol,
            confidence=Confidence.HIGH,
            reason="我的分析理由",
            stop_loss=...,
            take_profit=...,
            strategy_name=self.name,
            weight=self.weight
        )
```

然后在 `StrategyAgent` 中注册：

```python
self.strategies.append(MyStrategy())
```

### 添加新交易所

```python
# exchange/binance_exchange.py
from exchange.base_exchange import BaseExchange

class BinanceExchange(BaseExchange):
    async def initialize(self):
        # 初始化连接
        pass
    
    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        # 获取K线
        pass
    
    # 实现其他必需方法...
```

## ⚠️ 风险提示

- **测试模式**: 首次使用请务必开启测试模式 (`TEST_MODE=true`)
- **资金安全**: 请使用小额资金测试，确保策略有效后再增加资金
- **API 权限**: 建议使用只读 + 交易权限，不要授予提币权限
- **市场风险**: 加密货币市场波动剧烈，请谨慎投资

## 📊 监控面板功能

- **账户概览**: 余额、日盈亏、累计盈亏、胜率
- **价格走势**: 实时 K 线图表
- **持仓监控**: 当前持仓、盈亏状态
- **Agent 状态**: 各 Agent 运行状态
- **交易记录**: 历史交易列表
- **信号日志**: 策略生成的信号
- **实时日志**: 系统运行日志

## 🔧 配置说明

### 交易参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| TRADING_SYMBOL | 交易对 | DOGE/USDT:USDT |
| TRADING_AMOUNT | 每笔交易张数 | 100 |
| TRADING_LEVERAGE | 杠杆倍数 | 5 |
| TRADING_TIMEFRAME | K线周期 | 1m |
| TRADING_INTERVAL | 分析间隔(秒) | 120 |
| TEST_MODE | 测试模式 | true |

### 策略参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| ENABLED_STRATEGIES | 启用的策略 | ai_scalping |
| STRATEGY_MODE | 策略模式 | single |
| VOTE_THRESHOLD | 投票阈值 | 0.4 |

### LLM 预算参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| LLM_DAILY_TOKEN_LIMIT | 日 token 预算 | 200000 |
| LLM_PER_CALL_TOKEN_LIMIT | 单次调用 token 上限 | 4000 |

超出预算时 StrategyAgent 自动降级到纯技术指标策略，不再调用 LLM。

### 风控参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| MAX_POSITION_RATIO | 最大持仓比例 | 0.3 |
| STOP_LOSS_RATIO | 单笔止损比例 | 0.02 |
| DAILY_STOP_LOSS_RATIO | 日止损比例 | 0.05 |
| MAX_LEVERAGE | 最大杠杆倍数 | 10 |
| MAX_CONSECUTIVE_LOSSES | 连续亏损暂停 | 5 |
| MAX_DAILY_TRADES | 每日最大交易次数 | 50 |

## 📝 开发日志

- **v1.0** — Agent 框架 / DeepSeek 集成 / 技术指标策略 / 基础风控 / Web 监控面板
- **v2.0 — Harness V2 重构**（参见 `docs/HARNESS_V2_IMPLEMENTATION_GUIDE.md`）
  - 三层信号验证（schema / sanity / policy_gate）
  - LLM 预算管理 + 超预算自动降级
  - Context engineering：K线摘要、regime tagger、PromptBuilder + frozen snapshot
  - 全链路 trace（SQLite + FTS5）+ forward return 回填
  - Memory + Skills 基础设施（含 frontmatter `metadata.quant`）
  - HITL Telegram 审批门（桩）+ Lifecycle health/checkpoint
- **v2.1 — 回测框架 + 策略接入 Context 层**
  - `harness/backtest/` MVP CLI（replay / rerun）
  - 所有 AI 策略迁移至 `BaseAIStrategy` 模板
  - 新增 `PromptOnlyAIStrategy` 与 `prompt_only_ai_strategy.py`
  - Backtest 可视化前端（Vue3 + Vite）+ FastAPI 后端
- **v2.2 — LLM 决策灵活化 + Skill 演化闭环**（详见 `docs/spec/tasks.md`）
  - `decision_schema` 四态（EXECUTE / REJECT / ADJUST / HOLD）解放 LLM 决策空间
  - 策略侧统一写回 `skill_used`，trace `fine_regime` 列
  - Skill 混合检索（regime + tape_signature + 触发类型）
  - `evolution/attribution.py` 离线归因（skill × regime → `skill_performance` / `skill_regime_accuracy`）
  - Postmortem 9 类归因、双签 patch、Skill draft 与 Skill lifecycle 持久化

## 📄 许可证

MIT License

---

*如有问题或建议，欢迎提交 Issue 或 PR*
