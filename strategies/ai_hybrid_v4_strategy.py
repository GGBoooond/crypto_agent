"""
AI 混合剥头皮策略 V4（hybrid_v4）
架构定位: "Python 猎犬 (海选) + AI 狙击手 (精选) + 交易所条件单 (毫秒级止盈止损)"

设计理念:
1. Python 实时计算硬指标过滤 90% 的无效行情，AI 只在出现明确技术形态时被唤醒
2. 开仓时由 AI 给出绝对止盈止损价格，开仓后由交易所条件单管控
3. 持仓期间 AI 定期评估是否需要调整止盈止损（ADJUST/HOLD）

执行流程:
1. ``_compute_indicators``：计算 RSI/Bollinger/MACD/ATR/EMA50/200
2. ``_check_hard_trigger``：六类硬触发条件（超买超卖回归 + 趋势回踩 + 波动率突破）
3. ``_build_trigger_payload``：按 ``mode`` 分发组装开仓/持仓两套 payload
4. ``_extract_signal``：按 ``mode`` 分发解析 EXECUTE/REJECT 或 ADJUST/HOLD
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


_OPEN_DECISION_SCHEMA = (
    '{\n'
    '  "action": "EXECUTE_LONG | EXECUTE_SHORT | WAIT | REJECT | EXECUTE | REJECT",\n'
    '  "fine_regime": "string",\n'
    '  "confidence": "HIGH | MEDIUM | LOW",\n'
    '  "confidence_breakdown": {"trend": 0.0, "momentum": 0.0, "support_resistance": 0.0},\n'
    '  "key_observations": ["string"],\n'
    '  "reason": "string",\n'
    '  "tp_price": number,\n'
    '  "sl_price": number\n'
    '}'
)

_POSITION_DECISION_SCHEMA = (
    '{\n'
    '  "action": "ADJUST | HOLD",\n'
    '  "reason": "string",\n'
    '  "tp_price": number,\n'
    '  "sl_price": number\n'
    '}'
)


class AIHybridV4Strategy(BaseAIStrategy):
    """V4 — Python 海选 + AI 精选 + 条件单管控的混合剥头皮策略。"""

    # ---- Pipeline tuning ----
    MIN_KLINES = 50
    REQUIRES_HARD_TRIGGER = True
    MAX_TOKENS = 800
    TEMPERATURE = 0.2
    POSITION_CHECK_MAX_TOKENS = 600

    # ---- Prompt contract ----
    SYSTEM_ROLE_OVERRIDE = "你是专业的加密货币交易风控官。只输出 JSON，严格遵循 [DECISION_SCHEMA]。"

    def __init__(self, weight: float = 1.0):
        super().__init__(name="AIHybridV4Strategy", weight=weight)
        config = settings.get_strategy_config("ai_hybrid_v4")
        # 当前未直接消费这两个值，但保留以便后续扩展（如 metadata 上报）
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

        # 同向持仓时拒绝重复开仓；反向信号留给上层做平仓/反手判断
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
        if mode == "position_check":
            return self._build_position_payload(df, position or {})
        return self._build_open_payload(df, trigger_ctx)

    def _build_open_payload(
        self,
        df: pd.DataFrame,
        trigger_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])

        vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
        rvol = float(curr['volume']) / float(vol_ma) if vol_ma and vol_ma > 0 else 0.0
        ema50 = float(curr['ema50'])
        dist_ema50 = ((current_price - ema50) / ema50 * 100) if ema50 else 0.0

        signal_dir = trigger_ctx.get("signal_dir", "LONG")
        ref_tp, ref_sl = self._reference_tp_sl(signal_dir, current_price, atr)

        return {
            "mode": "open",
            "signal_dir": signal_dir,
            "trigger_reason": trigger_ctx.get("trigger", ""),
            "indicators": {
                "rsi": round(float(curr['rsi']), 2),
                "atr": round(atr, 6),
                "bb_width": round(float(curr['bb_width']), 5),
                "macd_hist": round(float(curr['hist']), 4),
                "dist_ema50_pct": round(dist_ema50, 2),
                "rvol": round(rvol, 2),
            },
            "ref_tp": round(ref_tp, 8),
            "ref_sl": round(ref_sl, 8),
            "current_price": round(current_price, 8),
            "decision_schema": _OPEN_DECISION_SCHEMA,
            "role_constraints": {
                "persona": "风控官",
                "risk_appetite": "balanced",
                "target_pnl_ratio": 1.5,
                "max_loss_pct": self.max_loss,
                "holding_horizon": "scalping",
            },
        }

    def _build_position_payload(
        self,
        df: pd.DataFrame,
        position: Dict[str, Any],
    ) -> Dict[str, Any]:
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])
        pos_side = str(position.get('side', '')).upper()
        entry_price = float(position.get('entry_price', 0) or 0)
        existing_tp = position.get('tp_price')
        existing_sl = position.get('sl_price')

        pnl_pct = self._compute_pnl_pct(pos_side, entry_price, current_price)
        ref_tp, ref_sl = self._reference_tp_sl(pos_side, current_price, atr)

        return {
            "mode": "position_check",
            "signal_dir": pos_side,
            "trigger_reason": "POSITION_CHECK",
            "indicators": {
                "atr": round(atr, 6),
                "bb_width": round(float(curr['bb_width']), 5),
                "rsi": round(float(curr['rsi']), 2),
                "pnl_pct": round(pnl_pct, 2),
            },
            "ref_tp": round(ref_tp, 8),
            "ref_sl": round(ref_sl, 8),
            "current_price": round(current_price, 8),
            "entry_price": round(entry_price, 8) if entry_price else None,
            "existing_tp": existing_tp,
            "existing_sl": existing_sl,
            "decision_schema": _POSITION_DECISION_SCHEMA,
            "role_constraints": {
                "persona": "风控官",
                "risk_appetite": "balanced",
                "target_pnl_ratio": 1.5,
                "max_loss_pct": self.max_loss,
                "holding_horizon": "scalping",
            },
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
        mode = trigger_payload.get("mode", "open")
        if mode == "position_check":
            return self._extract_position_signal(ai_decision, position, trigger_payload, symbol)
        return self._extract_open_signal(ai_decision, trigger_payload, symbol)

    def _extract_open_signal(
        self,
        ai_decision: Dict[str, Any],
        trigger_payload: Dict[str, Any],
        symbol: str,
    ) -> Optional[Signal]:
        action = str(ai_decision.get('action', 'REJECT')).upper()
        confidence_str = str(ai_decision.get('confidence', 'LOW')).upper()
        ai_reason = ai_decision.get('reason', 'AI无理由')

        signal_dir = self._resolve_action_direction(
            action, str(trigger_payload.get("signal_dir", "LONG"))
        )
        if signal_dir is None:
            logger.info(f"[{self.name}] AI拒绝信号: {ai_reason}")
            return None

        current_price = float(trigger_payload.get("current_price") or 0)
        atr = float(trigger_payload.get("indicators", {}).get("atr") or 0)

        tp_price = ai_decision.get('tp_price')
        sl_price = ai_decision.get('sl_price')

        if tp_price is None or sl_price is None:
            logger.warning(f"[{self.name}] AI未返回有效的止盈止损价格，使用默认值")
            tp_price, sl_price = self._reference_tp_sl(signal_dir, current_price, atr)
        else:
            try:
                tp_price = float(tp_price)
                sl_price = float(sl_price)
            except (TypeError, ValueError):
                logger.warning(f"[{self.name}] AI返回的止盈止损无法解析，丢弃信号")
                return None

        signal_type = SignalType.BUY if signal_dir == "LONG" else SignalType.SELL
        confidence = self._map_confidence(confidence_str)

        signal = Signal(
            signal_type=signal_type,
            symbol=symbol,
            confidence=confidence,
            reason=f"[HybridV4] Python触发: {trigger_payload.get('trigger_reason')} | AI确认: {ai_reason}",
            stop_loss=sl_price,
            take_profit=tp_price,
            amount=settings.trading_amount,
            strategy_name=self.name,
            weight=self.weight,
            metadata={
                "hybrid_log": {
                    "trigger": trigger_payload,
                    "ai_decision": ai_decision,
                    "indicators": trigger_payload.get("indicators", {}),
                },
                "place_tp_sl_orders": True,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "atr": atr,
            },
        )
        logger.success(
            f"[{self.name}] 信号生成! {signal_type.value} @ {current_price} | "
            f"TP: {tp_price:.5f} | SL: {sl_price:.5f}"
        )
        return signal

    def _extract_position_signal(
        self,
        ai_decision: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        trigger_payload: Dict[str, Any],
        symbol: str,
    ) -> Optional[Signal]:
        action = str(ai_decision.get('action', 'HOLD')).upper()
        ai_reason = ai_decision.get('reason', '')
        new_tp = ai_decision.get('tp_price')
        new_sl = ai_decision.get('sl_price')

        if action == "HOLD":
            logger.debug(f"[{self.name}] 止盈止损无需调整: {ai_reason}")
            return None

        if action != "ADJUST" or new_tp is None or new_sl is None:
            return None

        try:
            new_tp = float(new_tp)
            new_sl = float(new_sl)
        except (TypeError, ValueError):
            return None

        existing_tp = trigger_payload.get("existing_tp")
        existing_sl = trigger_payload.get("existing_sl")
        # 价格变化在 1e-5 以内视为噪音，避免反复挂撤单
        tp_changed = existing_tp is None or abs(new_tp - float(existing_tp)) > 1e-5
        sl_changed = existing_sl is None or abs(new_sl - float(existing_sl)) > 1e-5
        if not tp_changed and not sl_changed:
            logger.debug(f"[{self.name}] 止盈止损价格变化不大，跳过调整")
            return None

        logger.info(
            f"[{self.name}] 需要调整止盈止损: TP {existing_tp} -> {new_tp:.5f}, "
            f"SL {existing_sl} -> {new_sl:.5f}"
        )

        return Signal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            confidence=Confidence.MEDIUM,
            reason=f"[HybridV4] 调整止盈止损: {ai_reason}",
            stop_loss=new_sl,
            take_profit=new_tp,
            amount=0,
            strategy_name=self.name,
            weight=self.weight,
            metadata={
                "adjust_tp_sl": True,
                "tp_price": new_tp,
                "sl_price": new_sl,
                "old_tp": existing_tp,
                "old_sl": existing_sl,
                "ai_decision": ai_decision,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _reference_tp_sl(
        signal_dir: str, current_price: float, atr: float
    ) -> Tuple[float, float]:
        """1.2 倍 ATR 止盈 / 1.5 倍 ATR 止损：剥头皮高胜率档默认参数。"""
        if signal_dir == "LONG":
            return current_price + (atr * 1.2), current_price - (atr * 1.5)
        return current_price - (atr * 1.2), current_price + (atr * 1.5)

    @staticmethod
    def _compute_pnl_pct(
        pos_side: str, entry_price: float, current_price: float
    ) -> float:
        if entry_price <= 0:
            return 0.0
        if pos_side == "LONG":
            return (current_price - entry_price) / entry_price * 100
        return (entry_price - current_price) / entry_price * 100

    @staticmethod
    def _map_confidence(confidence_str: str) -> Confidence:
        return {
            'HIGH': Confidence.HIGH,
            'MEDIUM': Confidence.MEDIUM,
            'LOW': Confidence.LOW,
        }.get(confidence_str, Confidence.LOW)

    @staticmethod
    def _resolve_action_direction(action: str, fallback_dir: str) -> Optional[str]:
        if action == "EXECUTE_LONG":
            return "LONG"
        if action == "EXECUTE_SHORT":
            return "SHORT"
        if action == "EXECUTE":
            return fallback_dir
        return None
