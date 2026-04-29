"""
AI 趋势狙击手策略 (Trend Sniper)
架构特点: "Python 趋势过滤器 (严选) + AI 资深操盘手 (决断)"

设计理念:
1. 彻底摒弃高频剥头皮，转向右侧趋势交易/波段交易。
2. 核心目标：高盈亏比 (R:R > 1:2) 和 高胜率入场。
3. "狙击手"哲学：大部分时间在等待，只有当趋势、动量、成交量完美共振时才扣动扳机。

执行流程:
1. Python 计算 ADX(趋势强度), EMA200(长期趋势), RSI, MACD, Volume Profile。
2. 严格的硬过滤：ADX < 25 (震荡) 直接放弃；逆势直接放弃。
3. AI 角色：不再是激进的剥头皮者，而是冷静的趋势猎人，专注于识别"主升浪"的起点。
"""
import os
import json
import re
import time
import asyncio
import aiohttp
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from openai import AsyncOpenAI
import ccxt.async_support as ccxt
from loguru import logger

from .base_strategy import BaseStrategy
from core.state_store import StateStore
from core.message import Signal, SignalType, Confidence
from config import settings


class AITrendSniperStrategy(BaseStrategy):
    """
    AI 趋势狙击手策略 - 专为高盈亏比设计
    """
    
    def __init__(self, weight: float = 1.0):
        super().__init__(name="AITrendSniperStrategy", weight=weight)
        
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )
        
        # 策略专属配置
        # ADX 阈值: Wilder 原始定义 25=强趋势, 20=趋势存在。
        # 设为 18 是为了捕获"正在形成中"的趋势——ADX 从低位爬升到 18-20 时
        # 往往是趋势启动的早期阶段, 此时入场盈亏比最佳。
        # 低于 18 则噪音过多, 不再有统计优势。
        self.min_adx = 18.0
        self.min_target_pct = 3.0   # 最小止盈目标 (3%)
        
        # 移动止损配置 (ATR-based Trailing Stop)
        self._trail_atr_mult = 2.5       # 追踪距离: 2.5 倍 ATR
        self._trail_lookback = 10        # 追踪回看K线数 (取近期最高/最低价)
        self._trail_activate_atr = 1.0   # 至少浮盈 1 倍 ATR 才启动追踪
        self._trail_min_step_atr = 0.3   # 最小调整步长: 0.3 倍 ATR (防止频繁微调)
        
        # BTC 趋势数据 (轻量级公共API, 带缓存避免频繁请求)
        self._btc_cache: Optional[Dict[str, Any]] = None
        self._btc_cache_ts: float = 0
        self._btc_cache_ttl: float = 300  # 缓存5分钟
        self._ccxt_public: Optional[ccxt.okx] = None
        
    async def _fetch_btc_trend(self) -> Optional[Dict[str, Any]]:
        """
        获取 BTC 趋势方向 (使用公共API, 带缓存)
        返回: {"price", "change_24h", "trend": "上涨/下跌/横盘", "high_24h", "low_24h"}
        """
        now = time.time()
        if self._btc_cache and (now - self._btc_cache_ts) < self._btc_cache_ttl:
            return self._btc_cache
        
        try:
            # 懒初始化公共 ccxt 客户端 (无需认证, 与 OKXClientPool 保持一致的网络配置)
            if self._ccxt_public is None:
                os.environ.setdefault("AIOHTTP_NO_EXTENSIONS", "1")
                connector = aiohttp.TCPConnector(
                    resolver=aiohttp.ThreadedResolver(),
                    ttl_dns_cache=300,
                )
                self._ccxt_public = ccxt.okx({
                    'enableRateLimit': True,
                    'options': {
                        'defaultType': 'swap',
                        'fetchMarkets': ['swap'],  # 只加载永续合约市场, 避免 SPOT/OPTION 请求超时
                    },
                    'tcp_connector': connector,
                    'timeout': 15000,
                })
            
            ticker = await self._ccxt_public.fetch_ticker('BTC/USDT:USDT')
            
            change_24h = float(ticker.get('percentage', 0) or 0)
            if change_24h > 1.5:
                trend = "强势上涨"
            elif change_24h > 0.3:
                trend = "温和上涨"
            elif change_24h < -1.5:
                trend = "强势下跌"
            elif change_24h < -0.3:
                trend = "温和下跌"
            else:
                trend = "横盘震荡"
            
            self._btc_cache = {
                "price": float(ticker['last']),
                "change_24h": round(change_24h, 2),
                "trend": trend,
                "high_24h": float(ticker['high']),
                "low_24h": float(ticker['low']),
            }
            self._btc_cache_ts = now
            return self._btc_cache
            
        except Exception as e:
            logger.warning(f"[{self.name}] 获取BTC趋势失败(非致命): {e}")
            return self._btc_cache  # 返回旧缓存, 没有就是 None
    
    def _calculate_indicators(self, klines: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        计算趋势交易所需的深度指标
        """
        try:
            df = pd.DataFrame(klines)
            cols = ['open', 'high', 'low', 'close', 'volume']
            df[cols] = df[cols].astype(float)
            
            # --- 1. 趋势核心指标 ---
            # EMA 200 (牛熊分界线)
            df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
            
            # MACD (动量确认)
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['hist'] = df['macd'] - df['signal']
            
            # --- 2. 趋势强度指标 (ADX) --- 使用 Wilder 平滑法 (标准算法)
            period = 14
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            
            # Wilder 平滑 = EMA(alpha=1/period)，比 SMA 更灵敏、更符合标准定义
            atr = tr.ewm(alpha=1/period, adjust=False).mean()
            df['atr'] = atr
            
            up_move = df['high'] - df['high'].shift()
            down_move = df['low'].shift() - df['low']
            
            plus_dm = pd.Series(
                np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
                index=df.index
            )
            minus_dm = pd.Series(
                np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
                index=df.index
            )
            
            # Wilder 平滑 DM → DI
            plus_dm_smooth = plus_dm.ewm(alpha=1/period, adjust=False).mean()
            minus_dm_smooth = minus_dm.ewm(alpha=1/period, adjust=False).mean()
            # 防止 ATR=0 导致除零 (极端横盘时)
            atr_safe = atr.replace(0, np.nan)
            plus_di = 100 * plus_dm_smooth / atr_safe
            minus_di = 100 * minus_dm_smooth / atr_safe
            
            # DX → ADX (再做一次 Wilder 平滑)
            # 防止 plus_di + minus_di = 0 导致除零
            di_sum = plus_di + minus_di
            di_sum = di_sum.replace(0, np.nan)
            dx = (100 * np.abs(plus_di - minus_di) / di_sum).fillna(0)
            df['adx'] = dx.ewm(alpha=1/period, adjust=False).mean()
            
            # --- 3. 震荡与超买超卖 ---
            # RSI (同样使用 Wilder 平滑法，与标准 RSI 定义一致)
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # 布林带 (用于判断突破)
            df['std'] = df['close'].rolling(window=20).std()
            df['upper_bb'] = df['ema20'] + (df['std'] * 2)
            df['lower_bb'] = df['ema20'] - (df['std'] * 2)
            
            return df
            
        except Exception as e:
            logger.error(f"[{self.name}] 指标计算错误: {e}")
            return pd.DataFrame()

    def _check_trailing_stop(self, symbol: str, df: pd.DataFrame, position: Optional[Dict[str, Any]]) -> Optional[Signal]:
        """
        移动止损检查 - 持仓时动态保护利润
        
        算法：ATR 跟踪止损
        - LONG: trailing_stop = 近期最高价 - 2.5 * ATR，只向上移动
        - SHORT: trailing_stop = 近期最低价 + 2.5 * ATR，只向下移动
        - 至少浮盈 1 倍 ATR 后才启动追踪
        - 最小调整步长 0.3 倍 ATR，避免频繁微调
        """
        if not position or float(position.get('size', 0)) == 0:
            return None
        
        pos_side = position.get('side', '').lower()
        entry_price = float(position.get('entry_price', 0))
        current_sl = float(position.get('sl_price', 0) or 0)
        current_tp = float(position.get('tp_price', 0) or 0)
        
        if entry_price == 0:
            return None
        
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])
        
        if atr == 0 or np.isnan(atr):
            return None
        
        new_sl = 0.0
        
        if pos_side == 'long':
            # 前置条件: 价格至少盈利 1 倍 ATR 才启动追踪
            if current_price < entry_price + self._trail_activate_atr * atr:
                return None
            
            # 以近期最高价为锚点，回撤 2.5 倍 ATR 设止损
            recent_high = float(df['high'].tail(self._trail_lookback).max())
            new_sl = recent_high - self._trail_atr_mult * atr
            
            # 安全检查: 新止损不能高于现价 (否则会立即触发)
            if new_sl >= current_price:
                return None
            
            # 只向上移动，不向下移动
            if current_sl > 0 and new_sl <= current_sl:
                return None
            
            # 最小步长检查: 调整幅度至少 0.3 倍 ATR
            if current_sl > 0 and (new_sl - current_sl) < self._trail_min_step_atr * atr:
                return None
                
        elif pos_side == 'short':
            # 前置条件: 价格至少盈利 1 倍 ATR 才启动追踪
            if current_price > entry_price - self._trail_activate_atr * atr:
                return None
            
            # 以近期最低价为锚点，反弹 2.5 倍 ATR 设止损
            recent_low = float(df['low'].tail(self._trail_lookback).min())
            new_sl = recent_low + self._trail_atr_mult * atr
            
            # 安全检查: 新止损不能低于现价
            if new_sl <= current_price:
                return None
            
            # 只向下移动，不向上移动
            if current_sl > 0 and new_sl >= current_sl:
                return None
            
            # 最小步长检查
            if current_sl > 0 and (current_sl - new_sl) < self._trail_min_step_atr * atr:
                return None
        else:
            return None
        
        # 计算浮盈百分比 (用于日志)
        if pos_side == 'long':
            unrealized_pct = (current_price - entry_price) / entry_price * 100
        else:
            unrealized_pct = (entry_price - current_price) / entry_price * 100
        
        logger.info(
            f"[{self.name}] 移动止损触发 | {pos_side.upper()} | "
            f"浮盈:{unrealized_pct:.2f}% | "
            f"旧SL:{current_sl:.5f} → 新SL:{new_sl:.5f} | "
            f"现价:{current_price:.5f} | ATR:{atr:.5f}"
        )

        # 当前执行器调整 TP/SL 需要同时提供 TP 和 SL；若仓位没有 TP，跳过本次追踪，避免无效信号反复告警
        if current_tp <= 0:
            logger.debug(
                f"[{self.name}] 跳过移动止损: 当前持仓未设置TP | "
                f"{symbol} | side={pos_side} | new_sl={new_sl:.5f}"
            )
            return None
        
        return Signal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            confidence=Confidence.MEDIUM,
            reason=f"[Sniper] 移动止损 ({pos_side}) | 浮盈:{unrealized_pct:.1f}% | SL: {current_sl:.5f} → {new_sl:.5f}",
            stop_loss=new_sl,
            take_profit=current_tp if current_tp > 0 else None,
            amount=0,
            strategy_name=self.name,
            weight=self.weight,
            metadata={
                "adjust_tp_sl": True,
                "tp_price": current_tp if current_tp > 0 else None,
                "sl_price": new_sl,
                "old_tp": current_tp,
                "old_sl": current_sl,
                "trailing_info": {
                    "atr": round(atr, 5),
                    "trail_mult": self._trail_atr_mult,
                    "entry_price": entry_price,
                    "unrealized_pct": round(unrealized_pct, 2),
                }
            }
        )

    def _check_sniper_triggers(self, df: pd.DataFrame, position: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """
        [硬过滤] 狙击手筛选逻辑
        只有满足 趋势+动量+成交量 完美配合时才触发
        """
        if df.empty or len(df) < 200:
            return False, "", {}
            
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 1. 市场环境过滤器 (Market Regime)
        # 如果 ADX 太低，说明市场在死鱼震荡，不做
        if curr['adx'] < self.min_adx:
            # 除非是极度缩窄后的爆发(Squeeze)，否则不介入。这里简单起见，ADX低直接过滤
            return False, "", {}

        # 2. 趋势方向判定
        # 价格在 EMA200 之上 = 牛市区，只做多
        # 价格在 EMA200 之下 = 熊市区，只做空
        is_bull_market = curr['close'] > curr['ema200']
        is_bear_market = curr['close'] < curr['ema200']
        
        # 3. 相对成交量 (RVol)
        vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
        r_vol = curr['volume'] / vol_ma if vol_ma > 0 else 0
        
        trigger_reason = ""
        signal_dir = "NONE"
        
        # --- 策略 A: 趋势回调狙击 (Trend Pullback Sniper) ---
        # 逻辑：强趋势中，价格回调到 EMA50 附近，RSI 冷却，MACD 动量拐头
        #
        # 距 EMA50 窗口: (-2.5%, +2%)
        #   下界 -2.5%: DOGE 在强趋势中回调可以短暂跌穿 EMA50 (假跌破),
        #               只要 EMA200 方向未变 + MACD 拐头, 这是更优的抄底位。
        #   上界 +2%:   允许"浅回调"——趋势极强时, 价格仅回落到 EMA50 上方 2%
        #               就被买盘接住, 这也是有效的回调入场。
        #   窗口 4.5% vs 旧 2.5%: 提升约 80% 的捕获率, 同时 AI 终审把控质量。
        #
        # RSI 区间: 多头 (28, 55), 空头 (45, 72)
        #   下界 28(多): RSI 28-30 在上升趋势中不是超卖, 而是"深度回调",
        #               配合 MACD 拐头是强买点。低于 28 风险过高, 趋势可能已破。
        #   上界 55(多): RSI 50-55 在上升趋势中是"中性偏弱", 说明价格刚开始回落
        #               但未深度调整, 对于强势行情中的浅回调是合理入场区间。
        if is_bull_market:
            dist_to_ema50 = (curr['close'] - curr['ema50']) / curr['ema50']
            if (-0.025 < dist_to_ema50 < 0.02) and (28 < curr['rsi'] < 55) and (curr['hist'] > prev['hist']):
                 trigger_reason = "BULLISH_TREND_PULLBACK (EMA50 Support + RSI Reset)"
                 signal_dir = "LONG"
                 
        elif is_bear_market:
            dist_to_ema50 = (curr['close'] - curr['ema50']) / curr['ema50']
            if (-0.02 < dist_to_ema50 < 0.025) and (45 < curr['rsi'] < 72) and (curr['hist'] < prev['hist']):
                 trigger_reason = "BEARISH_TREND_PULLBACK (EMA50 Resistance + RSI Reset)"
                 signal_dir = "SHORT"

        # --- 策略 B: 强力突破跟随 (Power Breakout) ---
        # 逻辑：横盘收敛后，放量突破关键位 (布林带 + 趋势方向一致)
        #
        # RVol 阈值: 2.0 → 1.5
        #   旧值 2.0 (2 倍均量) 在 15m 级别过于严苛——DOGE 的 15m 成交量分布
        #   本身波动较大, 2 倍量能突增一天可能只出现 2-3 次且经常在盘中不重要的时段。
        #   1.5 倍 (即 50% 以上的量能放大) 在技术分析文献中 (Bulkowski 突破研究)
        #   已被证明是有效突破的统计显著门槛。
        #   同时还有布林带突破 + MACD 方向 + ADX 趋势三重过滤, 1.5x 不会引入过多噪音。
        if signal_dir == "NONE" and r_vol > 1.5:
            if is_bull_market and curr['close'] > curr['upper_bb'] and curr['hist'] > 0:
                trigger_reason = "BULLISH_POWER_BREAKOUT (Vol Surge + UpperBB)"
                signal_dir = "LONG"
            elif is_bear_market and curr['close'] < curr['lower_bb'] and curr['hist'] < 0:
                trigger_reason = "BEARISH_POWER_BREAKOUT (Vol Surge + LowerBB)"
                signal_dir = "SHORT"
        
        if signal_dir != "NONE":
            # 持仓检查
            if position and float(position.get('size', 0)) > 0:
                pos_side = position.get('side', '').upper()
                # 同向不加仓 (狙击手只开一枪)，反向则允许
                if (pos_side == 'LONG' and signal_dir == 'LONG') or \
                   (pos_side == 'SHORT' and signal_dir == 'SHORT'):
                    return False, "", {}

            return True, f"[{signal_dir}] {trigger_reason}", {
                "signal_dir": signal_dir,
                "trigger": trigger_reason,
                "adx": round(curr['adx'], 2),
                "rsi": round(curr['rsi'], 2),
                "r_vol": round(r_vol, 2),
                "ema_dist": round((curr['close'] - curr['ema200'])/curr['ema200']*100, 2),
                "atr": round(curr['atr'], 5)
            }
            
        return False, "", {}

    def _parse_ai_json(self, text: str) -> Dict[str, Any]:
        """
        鲁棒的 AI JSON 响应解析器。
        处理: markdown 代码块包裹、行内注释、尾逗号等常见问题。
        """
        if not text:
            raise ValueError("AI 返回空响应")
        
        # 1. 尝试提取 markdown 代码块中的 JSON
        code_block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if code_block_match:
            text = code_block_match.group(1).strip()
        
        # 2. 提取最外层的 {} 花括号
        brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if not brace_match:
            raise ValueError(f"AI 响应中未找到 JSON 对象: {text[:200]}")
        json_str = brace_match.group(0)
        
        # 3. 清理常见的非法 JSON 语法
        # 移除行内 // 注释
        json_str = re.sub(r'//.*?(?=\n|$)', '', json_str)
        # 移除尾逗号 (如 "value",} )
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
        
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"[{self.name}] JSON 解析失败: {e}, 原始文本: {json_str[:300]}")
            raise ValueError(f"JSON 解析失败: {e}")

    def _find_support_resistance(self, df: pd.DataFrame, n: int = 50) -> Dict[str, List[float]]:
        """
        计算近期关键支撑位和阻力位 (基于近 n 根K线的前高前低)
        """
        recent = df.tail(n)
        current_price = float(recent.iloc[-1]['close'])
        
        # 寻找局部高点和低点 (窗口=5)
        highs = []
        lows = []
        window = 5
        for i in range(window, len(recent) - window):
            # 局部高点: 当前high是窗口内最高
            if recent.iloc[i]['high'] == recent.iloc[i-window:i+window+1]['high'].max():
                highs.append(float(recent.iloc[i]['high']))
            # 局部低点: 当前low是窗口内最低
            if recent.iloc[i]['low'] == recent.iloc[i-window:i+window+1]['low'].min():
                lows.append(float(recent.iloc[i]['low']))
        
        # 筛选: 阻力位 > 现价, 支撑位 < 现价, 各取最近3个
        resistance = sorted(set(h for h in highs if h > current_price))[:3]
        support = sorted(set(l for l in lows if l < current_price), reverse=True)[:3]
        
        return {"resistance": resistance, "support": support}

    def _build_sniper_prompt(self, symbol: str, df: pd.DataFrame, context: Dict[str, Any],
                              market_data: Optional[Dict[str, Any]] = None,
                              btc_trend: Optional[Dict[str, Any]] = None,
                              position: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """
        构建 AI 研判 Prompt。
        返回: (system_prompt, user_prompt)
        """
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])
        
        # 动态计算目标价格
        # 目标：盈亏比至少 1:2，且绝对涨幅 > 3%
        min_target_price_dist = current_price * (self.min_target_pct / 100.0)
        
        if context['signal_dir'] == "LONG":
            # 止损放在 2倍 ATR 或 EMA50 下方
            sl_dist = max(atr * 2.0, min_target_price_dist / 2) # 保证至少 1:2 的空间
            ref_sl = current_price - sl_dist
            
            # 止盈至少 3%，或者 3倍 ATR
            tp_dist = max(min_target_price_dist, atr * 3.0) 
            ref_tp = current_price + tp_dist
        else:
            sl_dist = max(atr * 2.0, min_target_price_dist / 2)
            ref_sl = current_price + sl_dist
            
            tp_dist = max(min_target_price_dist, atr * 3.0)
            ref_tp = current_price - tp_dist
            
        # 格式化
        price_str = f"{current_price}"
        decimals = len(price_str.split('.')[1]) if '.' in price_str else 2
        price_fmt = f".{max(decimals, 5)}f"
        
        # K线形态 (提供最近 15 根，包含完整 OHLC + 影线分析)
        recent_candles = []
        vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
        for i in range(15):
            idx = -(15-i)
            row = df.iloc[idx]
            k_type = "阳" if row['close'] > row['open'] else "阴"
            vol_ratio = row['volume'] / vol_ma if vol_ma > 0 else 0
            # 计算影线比例
            body = abs(row['close'] - row['open'])
            full_range = row['high'] - row['low']
            if full_range > 0:
                upper_shadow = (row['high'] - max(row['close'], row['open'])) / full_range
                lower_shadow = (min(row['close'], row['open']) - row['low']) / full_range
            else:
                upper_shadow = 0
                lower_shadow = 0
            recent_candles.append(
                f"T{idx}: {k_type} | O:{row['open']:{price_fmt}} H:{row['high']:{price_fmt}} L:{row['low']:{price_fmt}} C:{row['close']:{price_fmt}} | 上影:{upper_shadow:.0%} 下影:{lower_shadow:.0%} | Vol:{vol_ratio:.1f}x"
            )

        # --- 市场上下文信息 ---
        timeframe = settings.trading_timeframe
        
        # 支撑位/阻力位
        sr_levels = self._find_support_resistance(df)
        support_str = ", ".join(f"{s:{price_fmt}}" for s in sr_levels['support']) if sr_levels['support'] else "未检测到明显支撑"
        resistance_str = ", ".join(f"{r:{price_fmt}}" for r in sr_levels['resistance']) if sr_levels['resistance'] else "未检测到明显阻力"
        
        # MACD 柱状图趋势 (最近5根的变化方向)
        hist_values = df['hist'].tail(5).tolist()
        hist_trend = []
        for i in range(1, len(hist_values)):
            diff = hist_values[i] - hist_values[i-1]
            hist_trend.append("↑" if diff > 0 else "↓")
        hist_trend_str = " → ".join(hist_trend)
        hist_latest = f"{hist_values[-1]:{price_fmt}}" if hist_values else "N/A"
        
        # 24h 行情数据 (来自 market_data / ticker)
        market_context_str = ""
        if market_data:
            h24_change = market_data.get('change', 'N/A')
            h24_high = market_data.get('high', 'N/A')
            h24_low = market_data.get('low', 'N/A')
            h24_vol = market_data.get('volume', 'N/A')
            market_context_str = f"""
- 24h涨跌幅: {h24_change}%
- 24h最高/最低: {h24_high} / {h24_low}
- 24h成交量: {h24_vol}"""

        # BTC 大盘趋势
        btc_context_str = ""
        if btc_trend:
            btc_context_str = f"""
- BTC现价: {btc_trend['price']}
- BTC 24h涨跌: {btc_trend['change_24h']}%
- BTC趋势判定: {btc_trend['trend']}"""

        # EMA 排列状态
        ema_order = "多头排列(EMA20>50>200)" if curr['ema20'] > curr['ema50'] > curr['ema200'] else \
                    "空头排列(EMA20<50<200)" if curr['ema20'] < curr['ema50'] < curr['ema200'] else \
                    "交叉/纠缠"

        # --- 构建 System Prompt (角色 + 规则 + 输出格式) ---
        system_prompt = f"""你是一名传奇的波段交易员(Swing Trader)，智商极高，风格稳健。
你的座右铭是："弱水三千，只取一瓢"。你只在胜率极高(>80%)且盈亏比极佳(>1:2)时出手。

## 核心规则
1. 目标利润必须超过 {self.min_target_pct}%。如果当前波动率不足以支撑，直接 REJECT。
2. 止损必须宽，扛得住常规震荡，不能被插针扫掉。
3. 入场确认:
   - 回调(Pullback)信号: 必须看到止跌证据（缩量、明显下影线、阳包阴等）。
   - 突破(Breakout)信号: 必须确认成交量巨大(RVol > 2.0)且实体饱满。
4. 拒绝垃圾时间: K线杂乱无章、长上影长下影交替出现 = 主力分歧，必须 REJECT。
5. 逆大盘趋势的信号需要格外谨慎。

## 输出格式
严格输出以下 JSON，不要附加任何其他文本:
{{
    "action": "EXECUTE 或 REJECT",
    "confidence": "HIGH 或 MEDIUM",
    "reason": "简要分析趋势结构、量价关系、K线形态，说明执行或拒绝的理由",
    "tp_price": 止盈价格(数字),
    "sl_price": 止损价格(数字)
}}"""

        # --- 构建 User Prompt (仅包含本次信号的具体数据) ---
        user_prompt = f"""【基本信息】
- 标的: {symbol}
- K线周期: {timeframe}
- 现价: {current_price:{price_fmt}}
- 信号方向: {context['signal_dir']} ({context['trigger']})

【趋势环境】
- 趋势强度(ADX): {context['adx']} (>25=强趋势, >40=极强趋势)
- 距离EMA200: {context['ema_dist']}%
- EMA排列: {ema_order}
- 波动率(ATR): {context['atr']:{price_fmt}}
- RSI: {context['rsi']}
- 相对成交量(RVol): {context['r_vol']}x{market_context_str}{btc_context_str}

【MACD动量】
- 柱状图最新值: {hist_latest}
- 近5根变化趋势: {hist_trend_str}

【关键价位】
- 近期阻力位: {resistance_str}
- 近期支撑位: {support_str}
- 参考止盈: {ref_tp:{price_fmt}} (距现价 {abs(ref_tp - current_price)/current_price*100:.2f}%)
- 参考止损: {ref_sl:{price_fmt}} (距现价 {abs(ref_sl - current_price)/current_price*100:.2f}%)

【K线形态 (近15根 {timeframe})】
{chr(10).join(recent_candles)}

请基于以上数据，结合K线形态(注意影线、实体大小、量价配合)做出决策。"""
        
        return system_prompt, user_prompt

    async def analyze(self, symbol: str, klines: List[Dict[str, Any]], market_data: Dict[str, Any], position: Optional[Dict[str, Any]] = None) -> Optional[Signal]:
        if not self.enabled:
            return None
            
        # 需要更多K线来计算 EMA200
        if not klines or len(klines) < 210:
            logger.warning(f"[{self.name}] K线数据不足(需210+): {len(klines) if klines else 0}")
            return None
            
        # 1. 计算指标
        df = self._calculate_indicators(klines)
        if df.empty:
            return None

        # ========== 2. 移动止损检查 (持仓时优先执行) ==========
        # 每次分析周期都检查，如果需要调整止损则立即返回调整信号
        trailing_signal = self._check_trailing_stop(symbol, df, position)
        if trailing_signal:
            return trailing_signal
            
        # ========== 3. 新入场信号筛选 ==========
        # 狙击手硬筛选
        is_triggered, reason, context = self._check_sniper_triggers(df, position)
        
        if not is_triggered:
            return None
            
        logger.info(f"[{self.name}] 发现狙击机会: {reason} | ADX:{context['adx']} | 请求AI确认...")
        
        # 3. 获取 BTC 大盘趋势 (非阻塞, 失败不影响主流程)
        btc_trend = await self._fetch_btc_trend()
        if btc_trend:
            logger.debug(f"[{self.name}] BTC趋势: {btc_trend['trend']} ({btc_trend['change_24h']}%)")
        
        # 4. AI 深度研判
        try:
            system_prompt, user_prompt = self._build_sniper_prompt(
                symbol, df, context, market_data=market_data,
                btc_trend=btc_trend, position=position
            )
            
            response = await self.client.chat.completions.create(
                model=settings.ai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1, # 极低温度，保持理性
                max_tokens=400
            )
            
            result_text = response.choices[0].message.content
            ai_decision = self._parse_ai_json(result_text)
            
            action = ai_decision.get('action', 'REJECT').upper()
            
            if action != "EXECUTE":
                logger.info(f"[{self.name}] AI放弃机会: {ai_decision.get('reason')}")
                return None
                
            tp = float(ai_decision['tp_price'])
            sl = float(ai_decision['sl_price'])
            
            # 最终安全检查：盈亏比是否合理？
            curr_price = df.iloc[-1]['close']
            potential_profit = abs(tp - curr_price) / curr_price
            
            if potential_profit < 0.025: # 如果AI给出的止盈小于 2.5% (留点buffer)，强制否决
                logger.warning(f"[{self.name}] AI给出的止盈空间({potential_profit*100:.2f}%)不足3%，否决交易")
                return None

            signal_type = SignalType.BUY if context['signal_dir'] == "LONG" else SignalType.SELL
            
            return Signal(
                signal_type=signal_type,
                symbol=symbol,
                confidence=Confidence.HIGH, # 狙击手发出的信号默认高置信度
                reason=f"[Sniper] {reason} | AI: {ai_decision.get('reason')}",
                stop_loss=sl,
                take_profit=tp,
                amount=settings.trading_amount,
                strategy_name=self.name,
                weight=self.weight,
                metadata={
                    "place_tp_sl_orders": True,
                    "sniper_data": context
                }
            )
            
        except Exception as e:
            logger.error(f"[{self.name}] 执行异常: {e}")
            return None
