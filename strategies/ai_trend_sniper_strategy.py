"""
AI 趋势狙击手策略（trend_sniper）
架构定位: "Python 趋势过滤器 (严选) + 本地 trailing stop (LLM 前短路) + AI 资深操盘手 (决断)"

设计理念:
1. 摒弃高频剥头皮，转向右侧趋势/波段交易，只在 ADX>18 且趋势成立时出手
2. 持仓时优先本地 trailing stop（避免每次都调 LLM），仅在 trailing 不动时让 AI 评估
3. AI 输出绝对止盈止损 + 1:2 盈亏比硬性校验，弱水三千只取一瓢

执行流程:
1. ``_compute_indicators``：EMA20/50/200 + Wilder ADX/ATR/RSI + 布林带
2. ``_on_position_pre_llm``：持仓 trailing stop 检查；命中即直接返 Signal 不调 LLM
3. ``_check_hard_trigger``：策略 A (趋势回调) + 策略 B (放量突破) 二选一
4. ``_collect_extra_payload``：异步抓 BTC 大盘趋势 + 计算支撑阻力位
5. ``_build_trigger_payload``：装配 ATR/RSI/ADX 指标 + EXTRA_CONTEXT + 绝对价 schema
6. ``_extract_signal``：解析 EXECUTE/REJECT，校验最小 2.5% 盈亏空间
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import ccxt.async_support as ccxt
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
    '  "confidence": "HIGH | MEDIUM",\n'
    '  "reason": "string",\n'
    '  "tp_price": number,\n'
    '  "sl_price": number\n'
    '}'
)

_SYSTEM_ROLE = (
    "你是一名传奇的波段交易员(Swing Trader)，智商极高，风格稳健。"
    "座右铭: 弱水三千，只取一瓢。只在胜率极高(>80%)且盈亏比极佳(>1:2)时出手。\n"
    "## 核心规则\n"
    "1. 目标利润必须超过 3%；当前波动率不足以支撑则直接 REJECT。\n"
    "2. 止损必须宽，扛得住常规震荡，不能被插针扫掉。\n"
    "3. 入场确认: 回调信号需止跌证据；突破信号需巨量(RVol>2.0) + 实体饱满。\n"
    "4. K 线杂乱无章 / 长上影长下影交替 = 主力分歧，必须 REJECT。\n"
    "5. 逆大盘趋势的信号需格外谨慎。\n"
    "只输出 JSON，严格遵循 [DECISION_SCHEMA]。"
)

# Trailing stop 配置：作为模块常量便于单测调参
_TRAIL_ATR_MULT = 2.5         # 追踪距离: 2.5 倍 ATR
_TRAIL_LOOKBACK = 10          # 取近 10 根 K 线的最高/最低价
_TRAIL_ACTIVATE_ATR = 1.0     # 至少浮盈 1 倍 ATR 才启动
_TRAIL_MIN_STEP_ATR = 0.3     # 最小调整步长，防止频繁微调

# BTC 趋势缓存
_BTC_CACHE_TTL_SECONDS = 300

# 最小止盈空间硬性校验：低于 2.5% 直接否决（留 buffer 给目标 3%）
_MIN_PROFIT_RATIO = 0.025


class AITrendSniperStrategy(BaseAIStrategy):
    """趋势狙击手 — Python 严选 + 本地 trailing stop + AI 决断的右侧趋势策略。"""

    # ---- Pipeline tuning ----
    MIN_KLINES = 210
    REQUIRES_HARD_TRIGGER = True
    MAX_TOKENS = 600
    TEMPERATURE = 0.1

    # ---- Prompt contract ----
    SYSTEM_ROLE_OVERRIDE = _SYSTEM_ROLE

    def __init__(self, weight: float = 1.0):
        super().__init__(name="AITrendSniperStrategy", weight=weight)
        # ADX < 18 视为震荡区，直接放弃；> 18 抓"正在形成中"的趋势
        self.min_adx = 18.0
        self.min_target_pct = 3.0

        self._btc_cache: Optional[Dict[str, Any]] = None
        self._btc_cache_ts: float = 0.0
        self._ccxt_public: Optional[ccxt.okx] = None

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

            df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()

            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['hist'] = df['macd'] - df['signal']

            # Wilder 平滑：alpha=1/period 比 SMA 更灵敏，符合标准 ADX/ATR/RSI 定义
            period = 14
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr = tr.ewm(alpha=1 / period, adjust=False).mean()
            df['atr'] = atr

            up_move = df['high'] - df['high'].shift()
            down_move = df['low'].shift() - df['low']
            plus_dm = pd.Series(
                np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
                index=df.index,
            )
            minus_dm = pd.Series(
                np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
                index=df.index,
            )
            plus_dm_smooth = plus_dm.ewm(alpha=1 / period, adjust=False).mean()
            minus_dm_smooth = minus_dm.ewm(alpha=1 / period, adjust=False).mean()
            atr_safe = atr.replace(0, np.nan)
            plus_di = 100 * plus_dm_smooth / atr_safe
            minus_di = 100 * minus_dm_smooth / atr_safe
            di_sum = (plus_di + minus_di).replace(0, np.nan)
            dx = (100 * np.abs(plus_di - minus_di) / di_sum).fillna(0)
            df['adx'] = dx.ewm(alpha=1 / period, adjust=False).mean()

            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).ewm(alpha=1 / period, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1 / period, adjust=False).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))

            df['std'] = df['close'].rolling(window=20).std()
            df['upper_bb'] = df['ema20'] + (df['std'] * 2)
            df['lower_bb'] = df['ema20'] - (df['std'] * 2)

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
        if df is None or df.empty or len(df) < 200:
            return False, "", {}

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # 市场环境过滤：ADX < 18 视为震荡，直接放弃
        if curr['adx'] < self.min_adx:
            return False, "", {}

        is_bull_market = curr['close'] > curr['ema200']
        is_bear_market = curr['close'] < curr['ema200']

        vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
        r_vol = curr['volume'] / vol_ma if vol_ma > 0 else 0

        trigger_reason = ""
        signal_dir = "NONE"

        # 策略 A — 趋势回调狙击：EMA50 附近 + RSI 冷却 + MACD 拐头
        # 距 EMA50 窗口: 多头 (-2.5%, +2%) / 空头 (-2%, +2.5%) — 强趋势中允许浅回调或假跌破
        # RSI 区间: 多头 (28, 55) / 空头 (45, 72) — 不是超卖/超买，而是"深度回调/反弹"
        if is_bull_market:
            dist = (curr['close'] - curr['ema50']) / curr['ema50']
            if (-0.025 < dist < 0.02) and (28 < curr['rsi'] < 55) and (curr['hist'] > prev['hist']):
                trigger_reason = "BULLISH_TREND_PULLBACK (EMA50 Support + RSI Reset)"
                signal_dir = "LONG"
        elif is_bear_market:
            dist = (curr['close'] - curr['ema50']) / curr['ema50']
            if (-0.02 < dist < 0.025) and (45 < curr['rsi'] < 72) and (curr['hist'] < prev['hist']):
                trigger_reason = "BEARISH_TREND_PULLBACK (EMA50 Resistance + RSI Reset)"
                signal_dir = "SHORT"

        # 策略 B — 强力突破跟随：横盘收敛后放量突破布林轨
        # RVol 阈值 1.5 (旧 2.0 在 15m 级别过于严苛)；同时被 ADX/MACD 三重过滤兜底
        if signal_dir == "NONE" and r_vol > 1.5:
            if is_bull_market and curr['close'] > curr['upper_bb'] and curr['hist'] > 0:
                trigger_reason = "BULLISH_POWER_BREAKOUT (Vol Surge + UpperBB)"
                signal_dir = "LONG"
            elif is_bear_market and curr['close'] < curr['lower_bb'] and curr['hist'] < 0:
                trigger_reason = "BEARISH_POWER_BREAKOUT (Vol Surge + LowerBB)"
                signal_dir = "SHORT"

        if signal_dir == "NONE":
            return False, "", {}

        # 同向不加仓：狙击手只开一枪
        if self._has_open_position(position):
            pos_side = str(position.get('side', '')).upper()
            if (pos_side == 'LONG' and signal_dir == 'LONG') or \
                    (pos_side == 'SHORT' and signal_dir == 'SHORT'):
                return False, "", {}

        return True, f"[{signal_dir}] {trigger_reason}", {
            "signal_dir": signal_dir,
            "trigger": trigger_reason,
            "adx": round(curr['adx'], 2),
            "rsi": round(curr['rsi'], 2),
            "r_vol": round(r_vol, 2),
            "ema_dist": round((curr['close'] - curr['ema200']) / curr['ema200'] * 100, 2),
            "atr": round(curr['atr'], 5),
        }

    # ------------------------------------------------------------------
    # Position pre-LLM hooks
    # ------------------------------------------------------------------
    async def _on_position_pre_llm(
        self,
        *,
        symbol: str,
        df: Optional[pd.DataFrame],
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Dict[str, Any],
        context: Optional[StrategyContext],
    ) -> Optional[Signal]:
        """ATR trailing stop —— 浮盈足够时本地决策，不必调 LLM。"""
        if df is None or df.empty:
            return None

        pos_side = str(position.get('side', '')).lower()
        entry_price = float(position.get('entry_price', 0))
        current_sl = float(position.get('sl_price', 0) or 0)
        current_tp = float(position.get('tp_price', 0) or 0)

        if entry_price <= 0:
            return None

        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])
        if atr == 0 or np.isnan(atr):
            return None

        new_sl = self._compute_trailing_sl(
            df=df,
            pos_side=pos_side,
            entry_price=entry_price,
            current_price=current_price,
            current_sl=current_sl,
            atr=atr,
        )
        if new_sl is None:
            return None

        # 当前执行器调整 TP/SL 需要同时提供 TP；若仓位无 TP 则跳过本次追踪
        if current_tp <= 0:
            logger.debug(
                f"[{self.name}] 跳过移动止损: 当前持仓未设置TP | "
                f"{symbol} | side={pos_side} | new_sl={new_sl:.5f}"
            )
            return None

        unrealized_pct = self._compute_pnl_pct(pos_side, entry_price, current_price)
        logger.info(
            f"[{self.name}] 移动止损触发 | {pos_side.upper()} | "
            f"浮盈:{unrealized_pct:.2f}% | "
            f"旧SL:{current_sl:.5f} → 新SL:{new_sl:.5f} | "
            f"现价:{current_price:.5f} | ATR:{atr:.5f}"
        )

        return Signal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            confidence=Confidence.MEDIUM,
            reason=f"[Sniper] 移动止损 ({pos_side}) | 浮盈:{unrealized_pct:.1f}% | "
                   f"SL: {current_sl:.5f} → {new_sl:.5f}",
            stop_loss=new_sl,
            take_profit=current_tp,
            amount=0,
            strategy_name=self.name,
            weight=self.weight,
            metadata={
                "adjust_tp_sl": True,
                "tp_price": current_tp,
                "sl_price": new_sl,
                "old_tp": current_tp,
                "old_sl": current_sl,
                "trailing_info": {
                    "atr": round(atr, 5),
                    "trail_mult": _TRAIL_ATR_MULT,
                    "entry_price": entry_price,
                    "unrealized_pct": round(unrealized_pct, 2),
                },
            },
        )

    # ------------------------------------------------------------------
    # Extra payload (async IO: BTC trend + S/R levels)
    # ------------------------------------------------------------------
    async def _collect_extra_payload(
        self,
        *,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        context: Optional[StrategyContext],
    ) -> Dict[str, Any]:
        df = self._compute_indicators(klines) if klines else None
        sr = self._find_support_resistance(df) if df is not None else {"support": [], "resistance": []}
        btc_trend = await self._fetch_btc_trend()

        extra_context: Dict[str, Any] = {
            "support": [round(s, 6) for s in sr.get("support", [])],
            "resistance": [round(r, 6) for r in sr.get("resistance", [])],
        }
        if btc_trend:
            extra_context["btc_trend"] = btc_trend

        return {"extra_context": extra_context}

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
        signal_dir = trigger_ctx.get("signal_dir", "LONG")

        ref_tp, ref_sl = self._reference_tp_sl(signal_dir, current_price, atr)

        payload: Dict[str, Any] = {
            "mode": mode,
            "signal_dir": signal_dir,
            "trigger_reason": trigger_ctx.get("trigger", ""),
            "indicators": {
                "adx": round(float(curr['adx']), 2),
                "rsi": round(float(curr['rsi']), 2),
                "atr": round(atr, 6),
                "macd_hist": round(float(curr['hist']), 6),
                "ema_dist_pct": round((current_price - curr['ema200']) / curr['ema200'] * 100, 2),
            },
            "current_price": round(current_price, 8),
            "ref_tp": round(ref_tp, 8),
            "ref_sl": round(ref_sl, 8),
            "decision_schema": _DECISION_SCHEMA,
        }
        if extra:
            payload.update(extra)
        return payload

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
        if action != "EXECUTE":
            logger.info(f"[{self.name}] AI 放弃机会: {ai_decision.get('reason')}")
            return None

        try:
            tp_price = float(ai_decision['tp_price'])
            sl_price = float(ai_decision['sl_price'])
        except (KeyError, TypeError, ValueError):
            logger.warning(f"[{self.name}] AI 未提供有效 tp_price/sl_price，丢弃信号")
            return None

        current_price = float(trigger_payload.get("current_price") or 0)
        if current_price <= 0:
            return None

        # 硬性盈亏比校验：低于 2.5% 直接否决（目标利润 3%，留 buffer）
        potential_profit = abs(tp_price - current_price) / current_price
        if potential_profit < _MIN_PROFIT_RATIO:
            logger.warning(
                f"[{self.name}] AI 给出的止盈空间({potential_profit*100:.2f}%)不足 "
                f"{_MIN_PROFIT_RATIO*100:.1f}%，否决交易"
            )
            return None

        signal_dir = trigger_payload.get("signal_dir", "LONG")
        signal_type = SignalType.BUY if signal_dir == "LONG" else SignalType.SELL

        signal = Signal(
            signal_type=signal_type,
            symbol=symbol,
            confidence=Confidence.HIGH,
            reason=f"[Sniper] {trigger_payload.get('trigger_reason')} | "
                   f"AI: {ai_decision.get('reason', '')}",
            stop_loss=sl_price,
            take_profit=tp_price,
            amount=settings.trading_amount,
            strategy_name=self.name,
            weight=self.weight,
            metadata={
                "place_tp_sl_orders": True,
                "sniper_data": trigger_payload.get("indicators", {}),
                "ai_decision": ai_decision,
            },
        )
        logger.success(
            f"[{self.name}] 狙击信号生成! {signal_type.value} @ {current_price:.5f} | "
            f"TP: {tp_price:.5f} | SL: {sl_price:.5f}"
        )
        return signal

    # ------------------------------------------------------------------
    # Internal helpers — Trailing stop math
    # ------------------------------------------------------------------
    def _compute_trailing_sl(
        self,
        *,
        df: pd.DataFrame,
        pos_side: str,
        entry_price: float,
        current_price: float,
        current_sl: float,
        atr: float,
    ) -> Optional[float]:
        """Return the new trailing SL or ``None`` when no adjustment is warranted."""
        if pos_side == 'long':
            if current_price < entry_price + _TRAIL_ACTIVATE_ATR * atr:
                return None
            recent_high = float(df['high'].tail(_TRAIL_LOOKBACK).max())
            new_sl = recent_high - _TRAIL_ATR_MULT * atr
            if new_sl >= current_price:
                return None
            if current_sl > 0 and new_sl <= current_sl:
                return None
            if current_sl > 0 and (new_sl - current_sl) < _TRAIL_MIN_STEP_ATR * atr:
                return None
            return new_sl

        if pos_side == 'short':
            if current_price > entry_price - _TRAIL_ACTIVATE_ATR * atr:
                return None
            recent_low = float(df['low'].tail(_TRAIL_LOOKBACK).min())
            new_sl = recent_low + _TRAIL_ATR_MULT * atr
            if new_sl <= current_price:
                return None
            if current_sl > 0 and new_sl >= current_sl:
                return None
            if current_sl > 0 and (current_sl - new_sl) < _TRAIL_MIN_STEP_ATR * atr:
                return None
            return new_sl

        return None

    @staticmethod
    def _compute_pnl_pct(
        pos_side: str, entry_price: float, current_price: float
    ) -> float:
        if entry_price <= 0:
            return 0.0
        if pos_side == 'long':
            return (current_price - entry_price) / entry_price * 100
        return (entry_price - current_price) / entry_price * 100

    # ------------------------------------------------------------------
    # Internal helpers — TP/SL reference, S/R levels, BTC trend
    # ------------------------------------------------------------------
    def _reference_tp_sl(
        self, signal_dir: str, current_price: float, atr: float
    ) -> Tuple[float, float]:
        min_target_dist = current_price * (self.min_target_pct / 100.0)
        sl_dist = max(atr * 2.0, min_target_dist / 2)
        tp_dist = max(min_target_dist, atr * 3.0)
        if signal_dir == "LONG":
            return current_price + tp_dist, current_price - sl_dist
        return current_price - tp_dist, current_price + sl_dist

    def _find_support_resistance(
        self, df: pd.DataFrame, n: int = 50
    ) -> Dict[str, List[float]]:
        """近 n 根 K 线的局部高/低点 → 支撑阻力位（最近 3 个）。"""
        recent = df.tail(n)
        if len(recent) < 11:
            return {"resistance": [], "support": []}

        current_price = float(recent.iloc[-1]['close'])
        highs: List[float] = []
        lows: List[float] = []
        window = 5
        for i in range(window, len(recent) - window):
            slice_ = recent.iloc[i - window: i + window + 1]
            if recent.iloc[i]['high'] == slice_['high'].max():
                highs.append(float(recent.iloc[i]['high']))
            if recent.iloc[i]['low'] == slice_['low'].min():
                lows.append(float(recent.iloc[i]['low']))

        resistance = sorted({h for h in highs if h > current_price})[:3]
        support = sorted({l for l in lows if l < current_price}, reverse=True)[:3]
        return {"resistance": resistance, "support": support}

    async def _fetch_btc_trend(self) -> Optional[Dict[str, Any]]:
        """缓存 5 分钟，避免每次 analyze 都打 OKX 公共 API。"""
        now = time.time()
        if self._btc_cache and (now - self._btc_cache_ts) < _BTC_CACHE_TTL_SECONDS:
            return self._btc_cache

        try:
            if self._ccxt_public is None:
                # 与 OKXClientPool 保持一致的网络配置；只加载 swap 市场避免 SPOT/OPTION 超时
                os.environ.setdefault("AIOHTTP_NO_EXTENSIONS", "1")
                connector = aiohttp.TCPConnector(
                    resolver=aiohttp.ThreadedResolver(),
                    ttl_dns_cache=300,
                )
                self._ccxt_public = ccxt.okx({
                    'enableRateLimit': True,
                    'options': {
                        'defaultType': 'swap',
                        'fetchMarkets': ['swap'],
                    },
                    'tcp_connector': connector,
                    'timeout': 15000,
                })

            ticker = await self._ccxt_public.fetch_ticker('BTC/USDT:USDT')
            change_24h = float(ticker.get('percentage', 0) or 0)
            trend = self._classify_btc_trend(change_24h)

            self._btc_cache = {
                "price": float(ticker['last']),
                "change_24h": round(change_24h, 2),
                "trend": trend,
                "high_24h": float(ticker['high']),
                "low_24h": float(ticker['low']),
            }
            self._btc_cache_ts = now
            return self._btc_cache

        except Exception as exc:
            logger.warning(f"[{self.name}] 获取BTC趋势失败(非致命): {exc}")
            return self._btc_cache

    @staticmethod
    def _classify_btc_trend(change_24h: float) -> str:
        if change_24h > 1.5:
            return "强势上涨"
        if change_24h > 0.3:
            return "温和上涨"
        if change_24h < -1.5:
            return "强势下跌"
        if change_24h < -0.3:
            return "温和下跌"
        return "横盘震荡"
