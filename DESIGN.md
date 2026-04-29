# Crypto Agent - 智能加密货币量化交易机器人

## 项目概述

Crypto Agent 是一个基于 AI Agent 架构的智能加密货币量化交易系统。项目采用模块化设计，集成多种 AI 分析能力，提供实时可视化监控，并具备完善的风控机制。

---

## 一、系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Crypto Agent System                          │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Web Dashboard (FastAPI + Vue)              │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │    │
│  │  │ 实时行情  │ │ 持仓监控  │ │ 交易记录  │ │   Agent日志      │ │    │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                  ▲                                   │
│                                  │ WebSocket                         │
│  ┌───────────────────────────────┴─────────────────────────────┐    │
│  │                     Orchestrator (协调器)                     │    │
│  │   管理所有Agent的生命周期、消息传递、状态同步                    │    │
│  └───────────────────────────────┬─────────────────────────────┘    │
│                                  │                                   │
│  ┌──────────────┬────────────────┼────────────────┬────────────┐    │
│  ▼              ▼                ▼                ▼            ▼    │
│ ┌────────┐ ┌─────────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐    │
│ │ Market │ │  Strategy   │ │   Risk   │ │ Executor │ │ Logger │    │
│ │ Agent  │ │   Agents    │ │  Agent   │ │  Agent   │ │ Agent  │    │
│ │        │ │             │ │          │ │          │ │        │    │
│ │获取行情 │ │ AI策略分析  │ │ 风险控制  │ │ 执行交易  │ │日志记录 │    │
│ └────┬───┘ └──────┬──────┘ └────┬─────┘ └────┬─────┘ └───┬────┘    │
│      │            │             │            │           │          │
│      ▼            ▼             ▼            ▼           ▼          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Shared State Store (状态存储)               │   │
│  │    持仓信息 | 交易历史 | 信号记录 | 风控参数 | 系统配置           │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                  │                                   │
│  ┌───────────────────────────────┴─────────────────────────────┐    │
│  │                    Exchange Adapter (交易所适配)               │    │
│  │   OKX | Binance | Bybit | ...  (通过CCXT统一接口)              │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、核心模块设计

### 2.1 Agent 基础架构

采用事件驱动 + 消息传递的 Agent 模式：

```python
# Agent 基类设计
class BaseAgent:
    - name: str                    # Agent 名称
    - state: AgentState            # 当前状态
    - message_queue: Queue         # 消息队列
    - run(): async                 # 主运行循环
    - handle_message(): async      # 处理消息
    - emit(): async                # 发送消息给其他Agent
```

### 2.2 各 Agent 职责

| Agent | 职责 | 输入 | 输出 |
|-------|------|------|------|
| **MarketAgent** | 获取实时行情、K线、深度数据 | 交易对配置 | 市场数据事件 |
| **StrategyAgent** | AI分析，生成交易信号 | 市场数据 | 交易信号 |
| **RiskAgent** | 风险评估、仓位控制、止盈止损 | 交易信号+持仓 | 风控决策 |
| **ExecutorAgent** | 执行下单、监控订单状态 | 风控通过的信号 | 订单结果 |
| **LoggerAgent** | 记录所有事件、生成报告 | 所有事件 | 日志文件 |

### 2.3 Strategy Agent 多策略设计

```python
class StrategyAgent:
    strategies = [
        TrendFollowingStrategy(),    # 趋势跟踪策略
        MeanReversionStrategy(),     # 均值回归策略
        AIAnalysisStrategy(),        # DeepSeek AI分析
        TechnicalIndicatorStrategy() # 技术指标策略
    ]
    
    # 策略投票机制
    def aggregate_signals(signals: List[Signal]) -> FinalSignal:
        # 加权投票，综合多个策略意见
        pass
```

---

## 三、AI 能力集成

### 3.1 DeepSeek AI 分析

- 多轮对话记忆，持续跟踪市场
- 结构化 Prompt 工程
- 置信度评估

### 3.2 本地技术指标计算

```python
class TechnicalIndicators:
    - RSI(period=14)           # 相对强弱指标
    - MACD(12, 26, 9)          # MACD指标
    - Bollinger_Bands(20, 2)   # 布林带
    - EMA(periods=[5,10,20])   # 指数移动均线
    - ATR(period=14)           # 平均真实波幅
```

### 3.3 信号融合

```
AI信号(40%) + 技术指标(30%) + 趋势分析(20%) + 市场情绪(10%)
    ↓
  加权融合
    ↓
  最终交易决策
```

---

## 四、风控系统

### 4.1 风控规则

| 规则 | 描述 | 默认值 |
|------|------|--------|
| 最大持仓比例 | 单币种最大持仓占总资金比例 | 20% |
| 单笔止损 | 单笔交易最大亏损 | 2% |
| 日止损 | 单日最大亏损 | 5% |
| 最大杠杆 | 允许的最大杠杆倍数 | 10x |
| 连续亏损暂停 | 连续N次亏损后暂停交易 | 3次 |

### 4.2 动态止盈止损

```python
class DynamicStopLoss:
    - 移动止损 (Trailing Stop)
    - ATR止损 (基于波动率)
    - 时间止损 (持仓时间过长)
```

---

## 五、可视化监控

### 5.1 技术选型

- **后端**: FastAPI (Python异步框架)
- **前端**: Vue 3 + Tailwind CSS
- **实时通信**: WebSocket
- **图表**: ECharts / Chart.js

### 5.2 Dashboard 功能

```
┌─────────────────────────────────────────────────────────────────┐
│  Crypto Agent Dashboard                        [运行中] 🟢       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────┐  ┌─────────────────────────────────┐   │
│  │   账户概览           │  │        K线图 + 交易标记          │   │
│  │  总资产: $10,000    │  │                                  │   │
│  │  今日盈亏: +$150    │  │    📈 [K线图表区域]              │   │
│  │  持仓: 0.1 BTC      │  │                                  │   │
│  └─────────────────────┘  └─────────────────────────────────┘   │
│                                                                  │
│  ┌────────────────────────────┐  ┌──────────────────────────┐   │
│  │      当前持仓               │  │     Agent 状态            │   │
│  │  BTC/USDT Long 0.1 +2.5%  │  │  MarketAgent    🟢 运行中  │   │
│  │  Entry: $95,000           │  │  StrategyAgent  🟢 运行中  │   │
│  │  PnL: +$25.00             │  │  RiskAgent      🟢 运行中  │   │
│  └────────────────────────────┘  └──────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    实时日志                                │   │
│  │  [10:30:15] StrategyAgent: BUY信号 BTC/USDT 置信度:HIGH   │   │
│  │  [10:30:16] RiskAgent: 风控通过，允许执行                   │   │
│  │  [10:30:17] ExecutorAgent: 下单成功 0.1 BTC @ $95,000     │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 六、项目目录结构

```
crypto_agent/
├── DESIGN.md                 # 设计文档
├── README.md                 # 使用说明
├── requirements.txt          # Python依赖
├── .env.example              # 环境变量模板
├── config/
│   ├── __init__.py
│   ├── settings.py           # 全局配置
│   └── trading_config.py     # 交易参数配置
├── core/
│   ├── __init__.py
│   ├── base_agent.py         # Agent基类
│   ├── orchestrator.py       # 协调器
│   ├── state_store.py        # 状态存储
│   └── message.py            # 消息定义
├── agents/
│   ├── __init__.py
│   ├── market_agent.py       # 行情Agent
│   ├── strategy_agent.py     # 策略Agent
│   ├── risk_agent.py         # 风控Agent
│   ├── executor_agent.py     # 执行Agent
│   └── logger_agent.py       # 日志Agent
├── strategies/
│   ├── __init__.py
│   ├── base_strategy.py      # 策略基类
│   ├── ai_strategy.py        # AI分析策略
│   ├── technical_strategy.py # 技术指标策略
│   └── trend_strategy.py     # 趋势跟踪策略
├── exchange/
│   ├── __init__.py
│   ├── base_exchange.py      # 交易所基类
│   └── okx_exchange.py       # OKX实现
├── indicators/
│   ├── __init__.py
│   └── technical.py          # 技术指标计算
├── risk/
│   ├── __init__.py
│   └── risk_manager.py       # 风控管理器
├── web/
│   ├── __init__.py
│   ├── app.py                # FastAPI应用
│   ├── routes.py             # API路由
│   ├── websocket.py          # WebSocket处理
│   └── static/
│       └── index.html        # 前端页面
├── utils/
│   ├── __init__.py
│   ├── logger.py             # 日志工具
│   └── helpers.py            # 辅助函数
└── main.py                   # 主入口
```

---

## 七、技术栈

| 组件 | 技术选型 | 说明 |
|------|----------|------|
| 语言 | Python 3.10+ | 主开发语言 |
| 异步框架 | asyncio | 高并发支持 |
| 交易所SDK | ccxt | 统一交易接口 |
| AI | DeepSeek API | 市场分析 |
| Web框架 | FastAPI | 高性能API |
| 前端 | Vue 3 + Tailwind | 现代UI |
| 数据分析 | pandas, numpy | 数据处理 |
| 指标计算 | ta-lib / pandas-ta | 技术指标 |
| 配置管理 | python-dotenv | 环境变量 |
| 日志 | loguru | 结构化日志 |

---

## 八、运行方式

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入API密钥

# 3. 启动系统
python main.py

# 4. 访问监控面板
# 浏览器打开 http://localhost:8888
```

---

## 九、扩展性设计

### 9.1 添加新策略
```python
# 继承BaseStrategy，实现analyze方法
class MyCustomStrategy(BaseStrategy):
    async def analyze(self, market_data) -> Signal:
        # 自定义分析逻辑
        pass
```

### 9.2 添加新交易所
```python
# 继承BaseExchange，实现标准接口
class BinanceExchange(BaseExchange):
    async def fetch_ohlcv(self, symbol, timeframe, limit):
        pass
    async def create_order(self, symbol, side, amount, price=None):
        pass
```

### 9.3 添加新Agent
```python
# 继承BaseAgent，定义消息处理逻辑
class MyCustomAgent(BaseAgent):
    async def handle_message(self, message: Message):
        # 自定义处理逻辑
        pass
```

---

## 十、安全注意事项

1. **API密钥安全**: 所有密钥通过环境变量管理，不硬编码
2. **测试模式**: 提供完整的测试模式，模拟交易不下真实订单
3. **风控优先**: 任何交易都必须通过风控Agent审批
4. **日志审计**: 完整记录所有操作，便于事后分析
5. **权限分离**: 读写权限分离，最小化API权限

---

*文档版本: 1.0*  
*创建日期: 2026-01-25*
