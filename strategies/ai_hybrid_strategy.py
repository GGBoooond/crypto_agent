"""
AI混合剥头皮策略 (V3)
架构特点: "Python 猎犬 (海选) + AI 狙击手 (精选)"

设计理念:
1. 剥头皮对速度要求极高，纯AI分析延迟太大，且成本高昂。
2. 使用硬编码(Python)实时过滤90%的无效行情，只在出现明确技术形态时唤醒AI。
3. AI的作用不再是"看图"，而是"排雷" —— 识别假突破、形态陷阱和市场情绪。

执行流程:
1. Python实时计算 RSI, Bollinger, MACD, ATR。
2. Python根据硬指标筛选潜在机会 (Trigger)。
3. 一旦触发，将当前上下文打包发送给 DeepSeek。
4. AI进行定性分析，确认是否开仓。
"""
import json
import asyncio
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from openai import AsyncOpenAI
from loguru import logger

from .base_strategy import BaseStrategy
from core.state_store import StateStore
from core.message import Signal, SignalType, Confidence
from config import settings


class AIHybridStrategy(BaseStrategy):
    """
    AI混合驱动剥头皮策略 (V3)
    """
    
    def __init__(self, weight: float = 1.0):
        super().__init__(name="AIHybridStrategy", weight=weight)
        
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )
        
        # 策略参数 (可配置)
        config = settings.get_strategy_config("ai_hybrid")
        self.min_profit = config.get("min_profit", 0.5)
        self.max_loss = config.get("max_loss", 0.8)
        
        # 记录上一次AI分析的时间，防止短时间内对同一信号重复请求
        self.last_ai_check_time = 0
        self.last_check_price = 0
    
    def _calculate_indicators(self, klines: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        计算全套技术指标
        返回包含指标的DataFrame
        """
        try:
            df = pd.DataFrame(klines)
            cols = ['open', 'high', 'low', 'close', 'volume']
            df[cols] = df[cols].astype(float)
            
            # 1. RSI (14)
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # 2. Bollinger Bands (20, 2)
            df['ma20'] = df['close'].rolling(window=20).mean()
            df['std'] = df['close'].rolling(window=20).std()
            df['upper_bb'] = df['ma20'] + (df['std'] * 2)
            df['lower_bb'] = df['ma20'] - (df['std'] * 2)
            df['bb_width'] = (df['upper_bb'] - df['lower_bb']) / df['ma20']
            
            # 3. MACD (12, 26, 9)
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['hist'] = df['macd'] - df['signal']
            
            # 4. ATR (14) - 用于止损止盈
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            df['atr'] = true_range.rolling(14).mean()
            
            # 5. EMA Trends
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
            
            return df
            
        except Exception as e:
            logger.error(f"[{self.name}] 指标计算错误: {e}")
            return pd.DataFrame()

    def _check_hard_triggers(self, df: pd.DataFrame, position: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """
        [第一层过滤器] Python 硬编码逻辑
        返回: (是否触发, 触发原因, 上下文数据)
        """
        if df.empty:
            return False, "", {}
            
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 基础状态
        price = curr['close']
        rsi = curr['rsi']
        
        # 获取持仓状态
        has_position = False
        pos_side = ""
        if position and float(position.get('size', 0)) > 0:
            has_position = True
            pos_side = position.get('side', '').lower()

        is_bullish_trend = price > curr['ema50']
        is_bearish_trend = price < curr['ema50']
        
        trigger_reason = ""
        signal_dir = "NONE" # LONG or SHORT
        
        # --- 触发逻辑 A: 超买超卖回归 (Mean Reversion) - 宽松版 ---
        # 原逻辑: RSI < 30. 现在放宽到 35，增加频率
        # 原逻辑: RSI > 70. 现在放宽到 65
        if rsi < 35 and price < curr['lower_bb']:
            trigger_reason = "OVERSOLD_BOUNCE (RSI<35 + LowerBB)"
            signal_dir = "LONG"
            
        elif rsi > 65 and price > curr['upper_bb']:
            trigger_reason = "OVERBOUGHT_DUMP (RSI>65 + UpperBB)"
            signal_dir = "SHORT"
            
        # --- 触发逻辑 B: 趋势回踩 (Trend Pullback) - 宽松版 ---
        # 允许 RSI 在 40-60 之间，不再强制严格的中轨触碰，只要接近即可
        # 假设接近定义为: 价格在 MA20 的 ±0.2% 范围内 (对于DOGE 0.12来说是 0.00024)
        elif is_bullish_trend and (curr['close'] <= curr['ma20'] * 1.002) and (40 < rsi < 60):
            trigger_reason = "BULLISH_PULLBACK (Trend Up + Near MA20)"
            signal_dir = "LONG"
            
        elif is_bearish_trend and (curr['close'] >= curr['ma20'] * 0.998) and (40 < rsi < 60):
            trigger_reason = "BEARISH_PULLBACK (Trend Down + Near MA20)"
            signal_dir = "SHORT"
            
        # --- 触发逻辑 C: 波动率突破 (Volatility Breakout) - 新增 ---
        # 布林带张口 + 价格突破上轨 + RSI强势区(>55)
        elif (curr['bb_width'] > prev['bb_width']) and (price > curr['upper_bb']) and (rsi > 55):
             trigger_reason = "VOLATILITY_BREAKOUT_UP (BB Widen + UpperBB)"
             signal_dir = "LONG"

        elif (curr['bb_width'] > prev['bb_width']) and (price < curr['lower_bb']) and (rsi < 45):
             trigger_reason = "VOLATILITY_BREAKOUT_DOWN (BB Widen + LowerBB)"
             signal_dir = "SHORT"
        
        if signal_dir != "NONE":
            # 严谨的持仓过滤逻辑 (双向检查)
            if has_position:
                # 1. 如果持有做多仓位，且信号是做多 -> 忽略 (防止重复开仓)
                if pos_side == "long" and signal_dir == "LONG":
                    return False, "", {}
                
                # 2. 如果持有做空仓位，且信号是做空 -> 忽略 (防止重复开仓)
                if pos_side == "short" and signal_dir == "SHORT":
                    return False, "", {}
                
                # 3. 如果持有仓位，且信号是反向 -> 允许 (这将触发平仓或反手)
                # 此时 trigger_reason 会被传递给 AI，AI 会看到 "当前持有 xxx 仓位" 并结合反向信号做出判断

            # 防止重复触发: 如果价格变化不大且时间间隔短，忽略
            return True, f"[{signal_dir}] {trigger_reason}", {
                "signal_dir": signal_dir,
                "trigger": trigger_reason,
                "rsi": round(rsi, 2),
                "bb_pos": "Below Lower" if price < curr['lower_bb'] else "Above Upper" if price > curr['upper_bb'] else "Inside",
                "trend": "BULLISH" if is_bullish_trend else "BEARISH",
                "macd_hist": round(curr['hist'], 4),
                "atr": round(curr['atr'], 4)
            }
            
        return False, "", {}

    def _build_ai_prompt(self, symbol: str, df: pd.DataFrame, trigger_context: Dict[str, Any], position: Optional[Dict[str, Any]] = None) -> str:
        """
        构建 V3 专用 Prompt (优化版)
        新增: 相对成交量(RVol), 更多K线(10根), 价格位置, 持仓状态
        """
        curr = df.iloc[-1]
        current_price = float(curr['close'])
        atr = float(curr['atr'])
        
        # 计算相对成交量 (Vol / MA20_Vol)
        vol_ma = df['volume'].rolling(window=20).mean().iloc[-1]
        current_vol = curr['volume']
        r_vol = current_vol / vol_ma if vol_ma > 0 else 0
        
        # 计算价格位置 (距离EMA50的乖离率)
        ema50 = curr['ema50']
        dist_ema50 = (curr['close'] - ema50) / ema50 * 100
        
        # 智能格式化精度 - 根据价格自动调整小数位
        price_str = f"{current_price}"
        decimals = len(price_str.split('.')[1]) if '.' in price_str else 2
        price_fmt = f".{max(decimals, 5)}f"  # 至少保留5位小数
        
        # 计算最近10根K线形态特征
        recent_candles = []
        for i in range(10):
            idx = -(10-i)
            row = df.iloc[idx]
            
            body = abs(row['close'] - row['open'])
            upper_shadow = row['high'] - max(row['close'], row['open'])
            lower_shadow = min(row['close'], row['open']) - row['low']
            
            k_type = "阳" if row['close'] > row['open'] else "阴"
            
            # 单根K线相对量
            vol_ratio = row['volume'] / vol_ma if vol_ma > 0 else 0
            vol_desc = f"{vol_ratio:.1f}x"
            
            recent_candles.append(
                f"T{idx}: {k_type} | O:{row['open']:{price_fmt}} C:{row['close']:{price_fmt}} H:{row['high']:{price_fmt}} L:{row['low']:{price_fmt}} | Vol:{vol_desc}"
            )
            
        # 趋势判断描述
        trend_status = "顺势" if (trigger_context['signal_dir']=='LONG' and dist_ema50>0) or \
                                 (trigger_context['signal_dir']=='SHORT' and dist_ema50<0) else "逆势博弈"

        # 持仓描述
        pos_str = "当前无持仓"
        if position and float(position.get('size', 0)) > 0:
            side = position['side'].upper()
            entry = float(position.get('entry_price', 0))
            pnl = float(position.get('unrealized_pnl', 0))
            
            # 计算盈亏百分比
            pnl_pct = 0.0
            if entry > 0:
                if side == 'LONG':
                    pnl_pct = (current_price - entry) / entry * 100
                else:
                    pnl_pct = (entry - current_price) / entry * 100
            
            pos_str = f"持有 {side} 仓位 | 入场价: {entry} | 当前浮盈: {pnl_pct:+.2f}% (${pnl:.2f})"

        prompt = f"""
身份设定：你是一名**激进的高频剥头皮交易员(Scalper)**。你的风格是"快进快出"，像鳄鱼一样捕捉猎物。你**不追求完美的信号**，只要有60%的概率就敢于尝试，并用严密的止损来控制风险。

【战场态势】
- 标的: {symbol}
- 信号方向: {trigger_context['signal_dir']}
- 触发原因: {trigger_context['trigger']}
- 趋势背景: 距离 EMA50 {dist_ema50:+.2f}% ({trend_status})
- 当前持仓: {pos_str}

【资金博弈数据】
- 相对成交量(RVol): {r_vol:.2f}x ( >1.2 视为活跃，适合剥头皮)
- 波动率(ATR): {atr:{price_fmt}}
- 布林位置: {curr['bb_width']:.4f} (带宽)

【微观K线磁带 (最近10根)】
{chr(10).join(recent_candles)}

【高频交易决策逻辑】
1. **胜率优先**: 剥头皮的核心是"积小胜为大胜"。我们宁愿要一个胜率 70% 盈亏比 1:1 的单子，也不要一个胜率 30% 盈亏比 1:3 的单子。
2. **抗噪能力**: 加密货币波动极大，过窄的止损(如 <1.0 ATR) 100% 会被噪音扫出去。必须给价格 1.5 倍 ATR 左右的呼吸空间。
3. **快速落袋**: 只要有 1.0-1.2 倍 ATR 的利润就应该开始止盈，不要贪婪地等待大趋势。
4. **动量第一**: 
   - 只要 RVol > 1.0 且价格顺着信号方向突破，即使形态一般，也可以 EXECUTE。
   - 只有在"极度缩量(RVol < 0.5)"且"形态完全停滞"时才 REJECT。
5. **持仓处理**: 
   - 如果当前有持仓且信号方向相反（如持多出空信号），请重点评估是否应该**止盈/止损平仓**。
   - 如果浮盈已超过 1.0%，应倾向于落袋为安。

【决策输出 (JSON)】
{{
    "action": "EXECUTE | REJECT", 
    "confidence": "HIGH | MEDIUM | LOW",
    "reason": "简短犀利，例如: '顺势回踩到位，T0虽无大阳但有企稳迹象，赔率合适，干！'",
    "stop_loss_adjust": 1.5,  // 关键修改：放大止损到 1.5-2.0 ATR，防止被噪音扫损
    "take_profit_adjust": 1.2 // 关键修改：缩小止盈到 1.0-1.5 ATR，追求高胜率落袋为安
}}
"""
        return prompt

    async def analyze(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]] = None
    ) -> Optional[Signal]:
        """
        V3 标准执行流
        """
        if not self.enabled:
            return None
            
        # 1. 数据准备 (需要至少50根K线计算指标)
        if not klines or len(klines) < 50:
            logger.warning(f"[{self.name}] K线数据不足(需50+): {len(klines) if klines else 0}")
            return None
            
        # 2. 计算指标
        df = self._calculate_indicators(klines)
        if df.empty:
            return None
            
        # 3. Python 第一层筛选 (Hard Filter)
        is_triggered, reason, context = self._check_hard_triggers(df, position)
        
        if not is_triggered:
            # 没触发硬指标，直接返回，不浪费 AI Token
            return None
            
        logger.info(f"[{self.name}] 触发Python信号: {reason} | 准备请求AI确认...")
        
        # 记录触发事件
        state_store = StateStore()
        await state_store.add_ai_event({
            "type": "trigger",
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "trigger": context['trigger'],
            "indicators": context,
            "status": "analyzing"
        })

        # 4. AI 第二层确认 (Soft Filter)
        try:
            prompt = self._build_ai_prompt(symbol, df, context, position)
            
            # 使用 asyncio.wait_for 设置超时，防止 AI API 卡住
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=settings.ai_model,
                        messages=[
                            {"role": "system", "content": "你是专业的加密货币交易风控官。只输出JSON。"},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.2,
                        max_tokens=200,
                        timeout=30  # OpenAI client 内部超时
                    ),
                    timeout=45  # asyncio 外层超时保护
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{self.name}] AI API 调用超时(45s)，跳过本次分析")
                return None
            
            result_text = response.choices[0].message.content
            
            # 解析 JSON
            start = result_text.find('{')
            end = result_text.rfind('}') + 1
            if start == -1:
                return None
            
            ai_decision = json.loads(result_text[start:end])
            
            action = ai_decision.get('action', 'REJECT').upper()
            confidence_str = ai_decision.get('confidence', 'LOW').upper()
            ai_reason = ai_decision.get('reason', 'AI无理由')
            
            # 记录AI决策结果
            await state_store.add_ai_event({
                "type": "result",
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "trigger": context['trigger'],
                "decision": action,
                "reason": ai_reason,
                "confidence": confidence_str,
                "raw_response": ai_decision
            })

            if action != "EXECUTE":
                logger.info(f"[{self.name}] AI拒绝信号: {ai_reason}")
                return None
                
            # 5. 构建最终信号
            curr_price = df.iloc[-1]['close']
            atr = df.iloc[-1]['atr']
            
            # 动态止损止盈 (默认值调整为高胜率模式)
            sl_mult = ai_decision.get('stop_loss_adjust', 1.5) # 默认放宽到 1.5
            tp_mult = ai_decision.get('take_profit_adjust', 1.2) # 默认收紧到 1.2
            
            signal_type = SignalType.BUY if context['signal_dir'] == "LONG" else SignalType.SELL
            
            stop_loss = curr_price - (atr * sl_mult) if signal_type == SignalType.BUY else curr_price + (atr * sl_mult)
            take_profit = curr_price + (atr * tp_mult) if signal_type == SignalType.BUY else curr_price - (atr * tp_mult)
            
            # 映射置信度
            conf_map = {'HIGH': Confidence.HIGH, 'MEDIUM': Confidence.MEDIUM, 'LOW': Confidence.LOW}
            
            signal = Signal(
                signal_type=signal_type,
                symbol=symbol,
                confidence=conf_map.get(confidence_str, Confidence.LOW),
                reason=f"[Hybrid] Python触发: {context['trigger']} | AI确认: {ai_reason}",
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                amount=settings.trading_amount,
                strategy_name=self.name,
                weight=self.weight,
                metadata={
                    "hybrid_log": {
                        "trigger": context,
                        "ai_decision": ai_decision,
                        "indicators": {
                            "rsi": context['rsi'],
                            "atr": float(atr)
                        }
                    }
                }
            )
            
            logger.success(f"[{self.name}] 信号生成! {signal_type.value} @ {curr_price} | 理由: {ai_reason}")
            return signal
            
        except Exception as e:
            logger.error(f"[{self.name}] AI分析异常: {e}")
            return None
