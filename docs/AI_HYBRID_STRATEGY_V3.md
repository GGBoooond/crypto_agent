# AI Hybrid Strategy (V3) - 架构说明文档

## 1. 核心理念：Python 猎犬 + AI 狙击手

V3 策略彻底重构了量化逻辑，解决了 V2 策略中 "AI 反应慢、成本高、过度拟合" 的核心痛点。我们采用了一种**分层（Layered）决策模型**。

| 层面 | 角色 | 工具 | 职责 | 速度 | 成本 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Layer 1** | **猎犬 (海选)** | Python (Pandas) | 全天候监控，基于数学指标筛选潜在信号 | 毫秒级 | 0 |
| **Layer 2** | **狙击手 (精选)** | AI (DeepSeek) | 对 Layer 1 发现的信号进行"陷阱识别"和"形态确认" | 秒级 | 低 (按需调用) |

---

## 2. 策略运作流程

### Step 1: 硬指标海选 (Hard Filter)
Python 脚本会实时计算每一根 K 线的 RSI, Bollinger Bands, MACD。只有满足以下**任一**严格条件，才会触发报警：

1.  **超买超卖回归 (Mean Reversion)**:
    *   **做多**: RSI < 30 且 价格跌破布林下轨 (Lower BB)。
    *   **做空**: RSI > 70 且 价格突破布林上轨 (Upper BB)。
2.  **趋势回踩 (Trend Pullback)**:
    *   **做多**: 处于上升趋势 (Price > EMA50) 且 价格回踩 MA20 中轨。
    *   **做空**: 处于下跌趋势 (Price < EMA50) 且 价格反弹至 MA20 中轨。

> **效果**: 过滤掉 95% 的垃圾时间（横盘、无意义波动），确保 AI 只处理高价值机会。

### Step 2: AI 定性分析 (Qualitative Analysis)
一旦 Python 触发信号，系统将打包当前的上下文（K线形态、成交量、波动率）发送给 AI，并询问以下核心问题：

1.  **形态确认**: "Python 告诉我这里超卖了，但你看 K 线形态，是有企稳迹象（如锤子线），还是正在加速下跌（接飞刀）？"
2.  **量能验证**: "成交量是否异常？如果是缩量阴跌，可能是假信号。"
3.  **陷阱识别**: "这是否是一个诱多/诱空陷阱？"

AI 输出决策：`EXECUTE` (执行) 或 `REJECT` (拒绝)。

### Step 3: 动态风控 (Dynamic Risk)
如果 AI 批准交易，Python 将根据 **ATR (平均真实波幅)** 自动计算止损止盈：
*   **止损**: `Entry Price ± (ATR * AI建议系数)`
*   **止盈**: `Entry Price ± (ATR * AI建议系数 * 1.5)`

---

## 3. 如何启用 V3 策略

在 `config/settings.py` 或启动逻辑中，将策略类替换为 `AIHybridStrategy`。

```python
# 示例配置
strategies = [
    AIHybridStrategy(weight=1.0)
]
```

## 4. 优势总结

1.  **极高的响应速度**: 信号发现是实时的，AI 仅用于最后的 Confirm，延迟被最小化。
2.  **极低的 Token 消耗**: 只有真正出现机会时才调用 AI，费用可能降低 90%。
3.  **逻辑严密**: 既有数学的严谨（Python），又有经验的直觉（AI）。

---
*Created by Crypto Agent Architect*
