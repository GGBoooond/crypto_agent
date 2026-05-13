"""
AI 剥头皮策略（scalping）
架构定位: "纯 prompt 驱动 + 标准 BUY/SELL/HOLD/CLOSE 决策"

设计理念:
1. 利用 AI 分析最近 K 线的微观结构（价格行为、成交量、波动率）
2. 不做 Python 硬触发，每次都直问 AI；适合高频小利润快进快出（0.3-0.5%）
3. 输出标准 BUY/SELL/HOLD/CLOSE 动作 + 绝对止盈止损价格

执行流程:
1. ``_compute_indicators``：计算 RSI/MACD/BB/EMA50/ATR 用于 prompt 注入
2. ``_check_hard_trigger``：默认放行（REQUIRES_HARD_TRIGGER=False）
3. ``_build_trigger_payload``：装配指标快照 + DECISION_SCHEMA + USER_INSTRUCTION
4. ``_extract_signal``：复用 PromptOnlyAIStrategy._extract_signal_default
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config import settings
from core.message import Signal
from harness.context import StrategyContext

from .prompt_only_ai_strategy import PromptOnlyAIStrategy


_DECISION_SCHEMA = (
    '{\n'
    '  "action": "BUY | SELL | HOLD | CLOSE",\n'
    '  "confidence": "HIGH | MEDIUM | LOW",\n'
    '  "reason": "格式: [策略] + [信号依据]",\n'
    '  "entry_price": number,\n'
    '  "stop_loss": number,\n'
    '  "take_profit": number\n'
    '}'
)

_USER_INSTRUCTION = (
    "【决策逻辑 - 必须严格执行】\n"
    "1. 开仓条件 (Aggressive but Logical):\n"
    "   - BUY:\n"
    "     A) 趋势回踩: 上升趋势中回踩 EMA20 或布林中轨, RSI < 50 回升\n"
    "     B) 超卖反弹: 价格触及布林下轨 + RSI < 30 + 出现阳线\n"
    "     C) 突破确认: 强力突破布林上轨 + 巨量(Vol > 2x)\n"
    "   - SELL:\n"
    "     A) 趋势受阻: 下跌趋势中反弹 EMA20 或布林中轨, RSI > 50 回落\n"
    "     B) 超买回调: 价格触及布林上轨 + RSI > 70 + 出现阴线\n"
    "     C) 跌破确认: 强力跌破布林下轨 + 巨量\n"
    "2. 平仓条件:\n"
    "   - 达到止盈目标 (ATR 1.5 倍或固定 %)\n"
    "   - 趋势反转 (MACD 死叉/金叉, 或跌破关键均线)\n"
    "   - 止损触发\n"
    "3. 高频交易原则:\n"
    "   - 不要过度犹豫: 符合任何一种微观形态立即开仓\n"
    "   - 60% 信心也值得尝试: 用小止损换博弈空间\n"
    "   - 只有市场完全横盘且 ATR 极低时才 HOLD"
)


class AIScalpingStrategy(PromptOnlyAIStrategy):
    """纯 prompt 驱动的剥头皮策略，AI 直接给出 BUY/SELL/HOLD/CLOSE 动作。"""

    # ---- Pipeline tuning ----
    MIN_KLINES = 50
    REQUIRES_HARD_TRIGGER = False
    MAX_TOKENS = 400
    TEMPERATURE = 0.3

    # ---- Prompt contract ----
    SYSTEM_ROLE_OVERRIDE = (
        "你是一个顶级高频量化交易员(IQ 160)，擅长剥头皮策略(Scalping)。"
        "任务是利用微观市场结构和技术指标捕捉短线利润(0.3%-1.0%)。只输出 JSON。"
    )
    DECISION_SCHEMA = _DECISION_SCHEMA
    USER_INSTRUCTION = _USER_INSTRUCTION

    def __init__(self, weight: float = 1.0):
        super().__init__(name="AIScalpingStrategy", weight=weight)
        config = settings.get_strategy_config("ai_scalping")
        self.min_profit = config.get("min_profit", 0.3)
        self.max_loss = config.get("max_loss", 0.5)
        self.hold_minutes = config.get("hold_minutes", 10)

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------
    def _compute_indicators(
        self, klines: List[Dict[str, Any]]
    ) -> Optional[pd.DataFrame]:
        try:
            df = pd.DataFrame(klines)
            cols = ['open', 'high', 'low', 'close', 'volume']
            df[cols] = df[cols].astype(float)

            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))

            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['hist'] = df['macd'] - df['signal']

            df['ma20'] = df['close'].rolling(window=20).mean()
            df['std'] = df['close'].rolling(window=20).std()
            df['upper_bb'] = df['ma20'] + (df['std'] * 2)
            df['lower_bb'] = df['ma20'] - (df['std'] * 2)
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()

            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            df['atr'] = true_range.rolling(14).mean()

            return df

        except Exception as exc:
            logger.error(f"[{self.name}] 指标计算错误: {exc}")
            return None

    # ------------------------------------------------------------------
    # Signal extraction (delegates to PromptOnlyAIStrategy default mapper)
    # ------------------------------------------------------------------
    def _extract_signal(
        self,
        ai_decision: Dict[str, Any],
        *,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
        trigger_payload: Optional[Dict[str, Any]],
    ) -> Optional[Signal]:
        current_price = float(klines[-1]['close']) if klines else None
        return self._extract_signal_default(
            ai_decision,
            symbol=symbol,
            position=position,
            current_price=current_price,
        )
