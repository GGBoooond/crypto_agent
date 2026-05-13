"""
AI 混合剥头皮策略 V3（hybrid）
架构定位: "Python 猎犬 (海选) + AI 狙击手 (精选)"

设计理念:
1. 剥头皮对速度要求极高，纯 AI 分析延迟太大且成本高昂
2. Python 实时硬指标过滤 90% 的无效行情，只在出现明确技术形态时唤醒 AI
3. AI 输出 ATR 倍数（stop_loss_adjust / take_profit_adjust），由策略侧落地为绝对价格

执行流程:
1. ``_compute_indicators``：计算 RSI/Bollinger/MACD/ATR/EMA50/200
2. ``_check_hard_trigger``：六类硬触发条件（与 V4 触发器一致，但持仓上限不同）
3. ``_build_trigger_payload``：装配指标快照 + ATR 系数 schema + 高频原则 USER_INSTRUCTION
4. ``_extract_signal``：解析 EXECUTE/REJECT，将 ATR 系数还原为绝对止盈止损价格
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from config import settings
from core.message import Confidence, Signal, SignalType
from harness.context import StrategyContext

from .base_ai_strategy import BaseAIStrategy


_DECISION_SCHEMA = (
    '{\n'
    '  "action": "EXECUTE | REJECT",\n'
    '  "confidence": "HIGH | MEDIUM | LOW",\n'
    '  "reason": "string",\n'
    '  "stop_loss_adjust": number,  // ATR multiplier for SL (建议 1.5-2.0)\n'
    '  "take_profit_adjust": number  // ATR multiplier for TP (建议 1.0-1.5)\n'
    '}'
)

_USER_INSTRUCTION = (
    "【高频交易决策逻辑】\n"
    "1. 胜率优先：积小胜为大胜，宁要 70% 胜率 1:1 的单子，不要 30% 胜率 1:3 的单子。\n"
    "2. 抗噪能力：止损不能 < 1.0 ATR（一定会被噪音扫损）；建议 1.5 倍 ATR 给呼吸空间。\n"
    "3. 快速落袋：1.0-1.2 倍 ATR 利润就开始止盈，不贪大趋势。\n"
    "4. 动量第一：RVol > 1.0 且顺势突破即可 EXECUTE；只有极度缩量(RVol<0.5)+形态停滞才 REJECT。\n"
    "5. 持仓处理：反向信号时重点评估是否平仓；浮盈 > 1.0% 倾向落袋。"
)


class AIHybridStrategy(BaseAIStrategy):
    """V3 — Python 海选 + AI 精选的混合剥头皮策略，输出 ATR 倍数。"""

    # ---- Pipeline tuning ----
    MIN_KLINES = 50
    REQUIRES_HARD_TRIGGER = True
    MAX_TOKENS = 400
    TEMPERATURE = 0.2

    # ---- Prompt contract ----
    SYSTEM_ROLE_OVERRIDE = (
        "你是一名激进的高频剥头皮交易员(Scalper)，风格快进快出。"
        "只输出 JSON，严格遵循 [DECISION_SCHEMA]。"
    )

    def __init__(self, weight: float = 1.0):
        super().__init__(name="AIHybridStrategy", weight=weight)
        config = settings.get_strategy_config("ai_hybrid")
        # 保留参数以便后续扩展，例如自适应止盈止损上限
        self.min_profit = config.get("min_profit", 0.5)
        self.max_loss = config.get("max_loss", 0.8)

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

            df['ma20'] = df['close'].rolling(window=20).mean()
            df['std'] = df['close'].rolling(window=20).std()
            df['upper_bb'] = df['ma20'] + (df['std'] * 2)
            df['lower_bb'] = df['ma20'] - (df['std'] * 2)
            df['bb_width'] = (df['upper_bb'] - df['lower_bb']) / df['ma20']

            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['hist'] = df['macd'] - df['signal']

            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            df['atr'] = true_range.rolling(14).mean()

            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

            return df

        except Exception as exc:
            logger.error(f"[{self.name}] 指标计算错误: {exc}")
            return None

    # ------------------------------------------------------------------
    # Hard triggers
    # ------------------------------------------------------------------
    def _check_hard_trigger(
        self,
        df: Optional[pd.DataFrame],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if df is None or df.empty:
            return False, "", {}

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        price = curr['close']
        rsi = curr['rsi']
        is_bullish_trend = price > curr['ema50']
        is_bearish_trend = price < curr['ema50']

        trigger_reason = ""
        signal_dir = "NONE"

        if rsi < 35 and price < curr['lower_bb']:
            trigger_reason = "OVERSOLD_BOUNCE (RSI<35 + LowerBB)"
            signal_dir = "LONG"
        elif rsi > 65 and price > curr['upper_bb']:
            trigger_reason = "OVERBOUGHT_DUMP (RSI>65 + UpperBB)"
            signal_dir = "SHORT"
        elif is_bullish_trend and (curr['close'] <= curr['ma20'] * 1.002) and (40 < rsi < 60):
            trigger_reason = "BULLISH_PULLBACK (Trend Up + Near MA20)"
            signal_dir = "LONG"
        elif is_bearish_trend and (curr['close'] >= curr['ma20'] * 0.998) and (40 < rsi < 60):
            trigger_reason = "BEARISH_PULLBACK (Trend Down + Near MA20)"
            signal_dir = "SHORT"
        elif (curr['bb_width'] > prev['bb_width']) and (price > curr['upper_bb']) and (rsi > 55):
            trigger_reason = "VOLATILITY_BREAKOUT_UP (BB Widen + UpperBB)"
            signal_dir = "LONG"
        elif (curr['bb_width'] > prev['bb_width']) and (price < curr['lower_bb']) and (rsi < 45):
            trigger_reason = "VOLATILITY_BREAKOUT_DOWN (BB Widen + LowerBB)"
            signal_dir = "SHORT"

        if signal_dir == "NONE":
            return False, "", {}

        # 同向持仓时拒绝重复开仓；反向信号给 AI 评估是否平仓/反手
        if self._has_open_position(position):
            pos_side = str(position.get('side', '')).lower()
            if pos_side == "long" and signal_dir == "LONG":
                return False, "", {}
            if pos_side == "short" and signal_dir == "SHORT":
                return False, "", {}

        return True, f"[{signal_dir}] {trigger_reason}", {
            "signal_dir": signal_dir,
            "trigger": trigger_reason,
            "rsi": round(rsi, 2),
            "bb_pos": (
                "Below Lower" if price < curr['lower_bb']
                else "Above Upper" if price > curr['upper_bb']
                else "Inside"
            ),
            "trend": "BULLISH" if is_bullish_trend else "BEARISH",
            "macd_hist": round(curr['hist'], 4),
            "atr": round(curr['atr'], 4),
        }

    # ------------------------------------------------------------------
    # Trigger payload
    # ------------------------------------------------------------------
    def _build_trigger_payload(
        self,
        *,
        df: Optional[pd.DataFrame],
        trigger_ctx: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        mode: str,
        extra: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if df is None or df.empty:
            return None
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])

        vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
        rvol = float(curr['volume']) / float(vol_ma) if vol_ma and vol_ma > 0 else 0.0
        ema50 = float(curr['ema50'])
        dist_ema50 = ((current_price - ema50) / ema50 * 100) if ema50 else 0.0

        return {
            "mode": mode,
            "signal_dir": trigger_ctx.get("signal_dir", "LONG"),
            "trigger_reason": trigger_ctx.get("trigger", ""),
            "indicators": {
                "rsi": round(float(curr['rsi']), 2),
                "atr": round(atr, 6),
                "bb_width": round(float(curr['bb_width']), 5),
                "macd_hist": round(float(curr['hist']), 4),
                "dist_ema50_pct": round(dist_ema50, 2),
                "rvol": round(rvol, 2),
            },
            "current_price": round(current_price, 8),
            "decision_schema": _DECISION_SCHEMA,
            "user_instruction": _USER_INSTRUCTION,
        }

    # ------------------------------------------------------------------
    # Signal extraction
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
        if not trigger_payload:
            return None

        action = str(ai_decision.get('action', 'REJECT')).upper()
        confidence_str = str(ai_decision.get('confidence', 'LOW')).upper()
        ai_reason = ai_decision.get('reason', 'AI无理由')

        if action != "EXECUTE":
            logger.info(f"[{self.name}] AI拒绝信号: {ai_reason}")
            return None

        signal_dir = trigger_payload.get("signal_dir", "LONG")
        current_price = float(trigger_payload.get("current_price") or 0)
        atr = float(trigger_payload.get("indicators", {}).get("atr") or 0)

        if current_price <= 0 or atr <= 0:
            logger.warning(f"[{self.name}] 缺少有效的 current_price/atr，丢弃信号")
            return None

        # 默认值：高胜率档（SL 1.5/TP 1.2）。AI 可在 1.0-2.5 范围内调整
        sl_mult = self._safe_float(ai_decision.get('stop_loss_adjust'), default=1.5)
        tp_mult = self._safe_float(ai_decision.get('take_profit_adjust'), default=1.2)

        signal_type = SignalType.BUY if signal_dir == "LONG" else SignalType.SELL
        if signal_type == SignalType.BUY:
            stop_loss = current_price - (atr * sl_mult)
            take_profit = current_price + (atr * tp_mult)
        else:
            stop_loss = current_price + (atr * sl_mult)
            take_profit = current_price - (atr * tp_mult)

        confidence = self._map_confidence(confidence_str)

        signal = Signal(
            signal_type=signal_type,
            symbol=symbol,
            confidence=confidence,
            reason=f"[Hybrid] Python触发: {trigger_payload.get('trigger_reason')} | AI确认: {ai_reason}",
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            amount=settings.trading_amount,
            strategy_name=self.name,
            weight=self.weight,
            metadata={
                "hybrid_log": {
                    "trigger": trigger_payload,
                    "ai_decision": ai_decision,
                    "indicators": trigger_payload.get("indicators", {}),
                },
                "atr": atr,
                "sl_mult": sl_mult,
                "tp_mult": tp_mult,
            },
        )
        logger.success(
            f"[{self.name}] 信号生成! {signal_type.value} @ {current_price:.5f} | "
            f"理由: {ai_reason}"
        )
        return signal

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_float(value: Any, *, default: float) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _map_confidence(confidence_str: str) -> Confidence:
        return {
            'HIGH': Confidence.HIGH,
            'MEDIUM': Confidence.MEDIUM,
            'LOW': Confidence.LOW,
        }.get(confidence_str, Confidence.LOW)
