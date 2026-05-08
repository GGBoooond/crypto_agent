"""
AI混合剥头皮策略 (V4) - 止盈止损挂单版
架构特点: "Python 猎犬 (海选) + AI 狙击手 (精选) + 交易所条件单 (毫秒级止盈止损)"

设计理念:
1. 继承 V3 的 Python + AI 混合架构
2. 新增: 开仓时同时挂止盈止损条件单，由交易所执行，响应速度毫秒级
3. 新增: 持仓时 AI 动态评估是否调整止盈止损位置

执行流程:
1. Python实时计算 RSI, Bollinger, MACD, ATR
2. Python根据硬指标筛选潜在机会 (Trigger)
3. 一旦触发，将上下文交给 BaseAIStrategy + PromptBuilder 组装 prompt（含 MEMORY/USER/SKILL/REGIME 层）
4. AI进行定性分析，确认是否开仓，并返回具体的止盈止损价格
5. 开仓后，Executor 同时挂止盈止损条件单
6. 持仓期间，AI 定期评估是否需要调整止盈止损

注：本版本通过 AI_PROMPT_MODE 环境变量切换 prompt 构建路径：
    - harness（默认）：走 BaseAIStrategy + PromptBuilder
    - legacy：使用本文件中的 _build_legacy_prompt() 兜底（紧急回滚）
"""
import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from config import settings
from core.message import Confidence, Signal, SignalType
from core.state_store import StateStore
from harness.context import StrategyContext

from .base_ai_strategy import BaseAIStrategy


class AIHybridV4Strategy(BaseAIStrategy):
    """AI混合驱动剥头皮策略 (V4) - 止盈止损挂单版"""

    def __init__(self, weight: float = 1.0):
        super().__init__(name="AIHybridV4Strategy", weight=weight)

        config = settings.get_strategy_config("ai_hybrid_v4")
        self.min_profit = config.get("min_profit", 0.5)
        self.max_loss = config.get("max_loss", 0.8)

        self.last_ai_check_time = 0
        self.last_check_price = 0

    # ------------------------------------------------------------------
    # Indicator computation & hard trigger filter (UNCHANGED IP)
    # ------------------------------------------------------------------
    def _calculate_indicators(self, klines: List[Dict[str, Any]]) -> pd.DataFrame:
        """计算全套技术指标，返回包含指标的DataFrame"""
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

        except Exception as e:
            logger.error(f"[{self.name}] 指标计算错误: {e}")
            return pd.DataFrame()

    def _check_hard_triggers(
        self,
        df: pd.DataFrame,
        position: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """[第一层过滤器] Python 硬编码逻辑，返回 (是否触发, 触发原因, 上下文数据)"""
        if df.empty:
            return False, "", {}

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        price = curr['close']
        rsi = curr['rsi']

        has_position = False
        pos_side = ""
        if position and float(position.get('size', 0)) > 0:
            has_position = True
            pos_side = position.get('side', '').lower()

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

        if signal_dir != "NONE":
            if has_position:
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

        return False, "", {}

    # ------------------------------------------------------------------
    # Trigger payload for harness PromptBuilder
    # ------------------------------------------------------------------
    def _build_trigger_payload_for_open(
        self,
        df: pd.DataFrame,
        trigger_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])

        vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
        rvol = float(curr['volume']) / float(vol_ma) if vol_ma and vol_ma > 0 else 0.0
        ema50 = float(curr['ema50'])
        dist_ema50 = ((current_price - ema50) / ema50 * 100) if ema50 else 0.0

        signal_dir = trigger_context['signal_dir']
        if signal_dir == "LONG":
            ref_tp = current_price + (atr * 1.2)
            ref_sl = current_price - (atr * 1.5)
        else:
            ref_tp = current_price - (atr * 1.2)
            ref_sl = current_price + (atr * 1.5)

        return {
            "mode": "open",
            "signal_dir": signal_dir,
            "trigger_reason": trigger_context['trigger'],
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
        }

    def _build_trigger_payload_for_position(
        self,
        df: pd.DataFrame,
        position: Dict[str, Any],
    ) -> Dict[str, Any]:
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])
        pos_side = position.get('side', '').upper()
        entry_price = float(position.get('entry_price', 0) or 0)
        existing_tp = position.get('tp_price')
        existing_sl = position.get('sl_price')

        pnl_pct = 0.0
        if entry_price > 0:
            if pos_side == "LONG":
                pnl_pct = (current_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - current_price) / entry_price * 100

        if pos_side == "LONG":
            ref_tp = current_price + (atr * 1.2)
            ref_sl = current_price - (atr * 1.5)
        else:
            ref_tp = current_price - (atr * 1.2)
            ref_sl = current_price + (atr * 1.5)

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
        }

    # ------------------------------------------------------------------
    # Legacy prompt (kept for AI_PROMPT_MODE=legacy fallback)
    # ------------------------------------------------------------------
    def _build_legacy_prompt(
        self,
        symbol: str,
        df: pd.DataFrame,
        trigger_context: Dict[str, Any],
        position: Optional[Dict[str, Any]] = None,
        is_position_check: bool = False,
    ) -> str:
        """V4 旧版 Prompt（仅在 AI_PROMPT_MODE=legacy 时使用）"""
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])

        vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
        current_vol = curr['volume']
        r_vol = current_vol / vol_ma if vol_ma > 0 else 0

        ema50 = curr['ema50']
        dist_ema50 = (curr['close'] - ema50) / ema50 * 100

        price_str = f"{current_price}"
        decimals = len(price_str.split('.')[1]) if '.' in price_str else 2
        price_fmt = f".{max(decimals, 5)}f"

        recent_candles = []
        for i in range(10):
            idx = -(10 - i)
            row = df.iloc[idx]
            k_type = "阳" if row['close'] > row['open'] else "阴"
            vol_ratio = row['volume'] / vol_ma if vol_ma > 0 else 0
            vol_desc = f"{vol_ratio:.1f}x"
            recent_candles.append(
                f"T{idx}: {k_type} | O:{row['open']:{price_fmt}} C:{row['close']:{price_fmt}} "
                f"H:{row['high']:{price_fmt}} L:{row['low']:{price_fmt}} | Vol:{vol_desc}"
            )

        if trigger_context.get('signal_dir') == "LONG":
            ref_tp = current_price + (atr * 1.2)
            ref_sl = current_price - (atr * 1.5)
        else:
            ref_tp = current_price - (atr * 1.2)
            ref_sl = current_price + (atr * 1.5)

        trend_status = (
            "顺势"
            if (
                (trigger_context.get('signal_dir') == 'LONG' and dist_ema50 > 0)
                or (trigger_context.get('signal_dir') == 'SHORT' and dist_ema50 < 0)
            )
            else "逆势博弈"
        )

        pos_str = "当前无持仓"
        pos_side = ""
        entry_price = 0.0
        pnl_pct = 0.0
        existing_tp = None
        existing_sl = None

        if position and float(position.get('size', 0)) > 0:
            pos_side = position['side'].upper()
            entry_price = float(position.get('entry_price', 0))
            existing_tp = position.get('tp_price')
            existing_sl = position.get('sl_price')
            if entry_price > 0:
                if pos_side == 'LONG':
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - current_price) / entry_price * 100
            pos_str = (
                f"持有 {pos_side} 仓位 | 入场价: {entry_price:{price_fmt}} | "
                f"当前浮盈: {pnl_pct:+.2f}%"
            )
            if existing_tp:
                pos_str += f" | 当前止盈单: {existing_tp:{price_fmt}}"
            if existing_sl:
                pos_str += f" | 当前止损单: {existing_sl:{price_fmt}}"

        if is_position_check and position:
            return f"""
身份设定：你是一名**激进的高频剥头皮交易员(Scalper)**，专注于动态管理持仓。

【当前持仓状态】
- 标的: {symbol}
- 持仓方向: {pos_side}
- 入场价: {entry_price:{price_fmt}}
- 当前价: {current_price:{price_fmt}}
- 浮动盈亏: {pnl_pct:+.2f}%
- 当前止盈单: {existing_tp if existing_tp else '未设置'}
- 当前止损单: {existing_sl if existing_sl else '未设置'}

【市场数据】
- ATR(14): {atr:{price_fmt}}
- 相对成交量(RVol): {r_vol:.2f}x
- 趋势背景: 距离 EMA50 {dist_ema50:+.2f}%
- 布林带宽: {curr['bb_width']:.4f}

【微观K线磁带 (最近10根)】
{chr(10).join(recent_candles)}

【决策输出 (JSON)】
{{
    "action": "ADJUST | HOLD",
    "reason": "string",
    "tp_price": {ref_tp:{price_fmt}},
    "sl_price": {ref_sl:{price_fmt}}
}}
"""

        return f"""
身份设定：你是一名**激进的高频剥头皮交易员(Scalper)**。

【战场态势】
- 标的: {symbol}
- 当前价格: {current_price:{price_fmt}}
- 信号方向: {trigger_context['signal_dir']}
- 触发原因: {trigger_context['trigger']}
- 趋势背景: 距离 EMA50 {dist_ema50:+.2f}% ({trend_status})
- 当前持仓: {pos_str}

【风险参考数据】
- ATR(14): {atr:{price_fmt}}
- 参考止盈位: {ref_tp:{price_fmt}}
- 参考止损位: {ref_sl:{price_fmt}}
- 相对成交量(RVol): {r_vol:.2f}x

【微观K线磁带 (最近10根)】
{chr(10).join(recent_candles)}

【决策输出 (JSON)】
{{
    "action": "EXECUTE | REJECT",
    "confidence": "HIGH | MEDIUM | LOW",
    "reason": "string",
    "tp_price": {ref_tp:{price_fmt}},
    "sl_price": {ref_sl:{price_fmt}}
}}
"""

    # ------------------------------------------------------------------
    # Decision -> Signal extraction
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

        if action != "EXECUTE":
            logger.info(f"[{self.name}] AI拒绝信号: {ai_reason}")
            return None

        current_price = float(trigger_payload.get("current_price") or 0)
        atr = float(trigger_payload.get("indicators", {}).get("atr") or 0)
        signal_dir = trigger_payload.get("signal_dir", "LONG")

        tp_price = ai_decision.get('tp_price')
        sl_price = ai_decision.get('sl_price')

        if tp_price is None or sl_price is None:
            logger.warning(f"[{self.name}] AI未返回有效的止盈止损价格，使用默认值")
            if signal_dir == "LONG":
                tp_price = current_price + (atr * 1.2)
                sl_price = current_price - (atr * 1.5)
            else:
                tp_price = current_price - (atr * 1.2)
                sl_price = current_price + (atr * 1.5)
        else:
            try:
                tp_price = float(tp_price)
                sl_price = float(sl_price)
            except (TypeError, ValueError):
                logger.warning(f"[{self.name}] AI返回的止盈止损无法解析，丢弃信号")
                return None

        signal_type = SignalType.BUY if signal_dir == "LONG" else SignalType.SELL
        conf_map = {
            'HIGH': Confidence.HIGH,
            'MEDIUM': Confidence.MEDIUM,
            'LOW': Confidence.LOW,
        }

        signal = Signal(
            signal_type=signal_type,
            symbol=symbol,
            confidence=conf_map.get(confidence_str, Confidence.LOW),
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
    # Orchestration
    # ------------------------------------------------------------------
    async def analyze(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]] = None,
        context: Optional[StrategyContext] = None,
    ) -> Optional[Signal]:
        if not self.enabled:
            return None

        if not klines or len(klines) < 50:
            logger.warning(
                f"[{self.name}] K线数据不足(需50+): {len(klines) if klines else 0}"
            )
            return None

        df = self._calculate_indicators(klines)
        if df.empty:
            return None

        state_store = StateStore()

        if position and float(position.get('size', 0)) > 0:
            return await self._handle_position_check(symbol, df, klines, market_data, position, context, state_store)

        is_triggered, reason, trig_ctx = self._check_hard_triggers(df, position)
        if not is_triggered:
            return None

        logger.info(f"[{self.name}] 触发Python信号: {reason} | 准备请求AI确认...")
        await state_store.add_ai_event({
            "type": "trigger",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "trigger": trig_ctx['trigger'],
            "indicators": trig_ctx,
            "status": "analyzing",
        })

        prompt_mode = (settings.ai_prompt_mode or "harness").lower()
        if prompt_mode == "legacy":
            return await self._analyze_legacy_open(symbol, df, trig_ctx, position, state_store)

        trigger_payload = self._build_trigger_payload_for_open(df, trig_ctx)
        try:
            signal = await self._run_llm(
                symbol=symbol,
                klines=klines,
                market_data=market_data,
                position=position,
                context=context,
                trigger_payload=trigger_payload,
                max_tokens=300,
                temperature=0.2,
                system_role_override=(
                    "你是专业的加密货币交易风控官。只输出 JSON，"
                    "价格精度保持5位小数。严格遵循 [DECISION_SCHEMA]。"
                ),
                indicators_df=df,
            )
        except Exception as e:
            logger.error(f"[{self.name}] AI分析异常: {e}")
            return None

        await state_store.add_ai_event({
            "type": "result",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "trigger": trig_ctx['trigger'],
            "decision": "EXECUTE" if signal else "REJECT",
            "reason": signal.reason if signal else "rejected/empty",
            "tp_price": signal.take_profit if signal else None,
            "sl_price": signal.stop_loss if signal else None,
            "llm_usage": self._last_llm_usage,
        })
        return signal

    async def _handle_position_check(
        self,
        symbol: str,
        df: pd.DataFrame,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Dict[str, Any],
        context: Optional[StrategyContext],
        state_store: StateStore,
    ) -> Optional[Signal]:
        prompt_mode = (settings.ai_prompt_mode or "harness").lower()
        if prompt_mode == "legacy":
            return await self._analyze_legacy_position(symbol, df, position, state_store)

        trigger_payload = self._build_trigger_payload_for_position(df, position)
        try:
            signal = await self._run_llm(
                symbol=symbol,
                klines=klines,
                market_data=market_data,
                position=position,
                context=context,
                trigger_payload=trigger_payload,
                max_tokens=200,
                temperature=0.2,
                system_role_override=(
                    "你是专业的加密货币交易风控官。只输出 JSON，"
                    "价格精度保持5位小数。严格遵循 [DECISION_SCHEMA]。"
                ),
                indicators_df=df,
            )
        except Exception as e:
            logger.error(f"[{self.name}] 持仓检查异常: {e}")
            return None

        await state_store.add_ai_event({
            "type": "position_check",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "action": "ADJUST" if signal else "HOLD",
            "new_tp": signal.take_profit if signal else None,
            "new_sl": signal.stop_loss if signal else None,
            "old_tp": position.get("tp_price"),
            "old_sl": position.get("sl_price"),
            "llm_usage": self._last_llm_usage,
        })
        return signal

    # ------------------------------------------------------------------
    # Legacy execution paths (only when AI_PROMPT_MODE=legacy)
    # ------------------------------------------------------------------
    async def _analyze_legacy_open(
        self,
        symbol: str,
        df: pd.DataFrame,
        trig_ctx: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        state_store: StateStore,
    ) -> Optional[Signal]:
        prompt = self._build_legacy_prompt(symbol, df, trig_ctx, position, is_position_check=False)
        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=settings.ai_model,
                    messages=[
                        {"role": "system", "content": "你是专业的加密货币交易风控官。只输出JSON，价格精度保持5位小数。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=300,
                    timeout=30,
                ),
                timeout=45,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{self.name}] [legacy] AI API 调用超时(45s)，跳过本次分析")
            return None
        except Exception as e:
            logger.error(f"[{self.name}] [legacy] AI分析异常: {e}")
            return None

        try:
            ai_decision = self._extract_json(response.choices[0].message.content or "")
        except Exception:
            return None
        if not ai_decision:
            return None

        trigger_payload = self._build_trigger_payload_for_open(df, trig_ctx)
        return self._extract_open_signal(ai_decision, trigger_payload, symbol)

    async def _analyze_legacy_position(
        self,
        symbol: str,
        df: pd.DataFrame,
        position: Dict[str, Any],
        state_store: StateStore,
    ) -> Optional[Signal]:
        trig_ctx = {
            "signal_dir": position.get("side", "").upper(),
            "trigger": "POSITION_CHECK",
        }
        prompt = self._build_legacy_prompt(symbol, df, trig_ctx, position, is_position_check=True)
        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=settings.ai_model,
                    messages=[
                        {"role": "system", "content": "你是专业的加密货币交易风控官。只输出JSON，价格精度保持5位小数。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=200,
                    timeout=30,
                ),
                timeout=45,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{self.name}] [legacy] 持仓检查 AI API 超时，跳过调整")
            return None
        except Exception as e:
            logger.error(f"[{self.name}] [legacy] 持仓检查异常: {e}")
            return None

        try:
            ai_decision = self._extract_json(response.choices[0].message.content or "")
        except Exception:
            return None
        if not ai_decision:
            return None

        trigger_payload = self._build_trigger_payload_for_position(df, position)
        return self._extract_position_signal(ai_decision, position, trigger_payload, symbol)
