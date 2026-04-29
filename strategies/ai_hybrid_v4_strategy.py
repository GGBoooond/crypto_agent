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
3. 一旦触发，将当前上下文打包发送给 DeepSeek
4. AI进行定性分析，确认是否开仓，并返回具体的止盈止损价格
5. 开仓后，Executor 同时挂止盈止损条件单
6. 持仓期间，AI 定期评估是否需要调整止盈止损
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


class AIHybridV4Strategy(BaseStrategy):
    """
    AI混合驱动剥头皮策略 (V4) - 止盈止损挂单版
    """
    
    def __init__(self, weight: float = 1.0):
        super().__init__(name="AIHybridV4Strategy", weight=weight)
        
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )
        
        # 策略参数 (可配置)
        config = settings.get_strategy_config("ai_hybrid_v4")
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
        if rsi < 35 and price < curr['lower_bb']:
            trigger_reason = "OVERSOLD_BOUNCE (RSI<35 + LowerBB)"
            signal_dir = "LONG"
            
        elif rsi > 65 and price > curr['upper_bb']:
            trigger_reason = "OVERBOUGHT_DUMP (RSI>65 + UpperBB)"
            signal_dir = "SHORT"
            
        # --- 触发逻辑 B: 趋势回踩 (Trend Pullback) - 宽松版 ---
        elif is_bullish_trend and (curr['close'] <= curr['ma20'] * 1.002) and (40 < rsi < 60):
            trigger_reason = "BULLISH_PULLBACK (Trend Up + Near MA20)"
            signal_dir = "LONG"
            
        elif is_bearish_trend and (curr['close'] >= curr['ma20'] * 0.998) and (40 < rsi < 60):
            trigger_reason = "BEARISH_PULLBACK (Trend Down + Near MA20)"
            signal_dir = "SHORT"
            
        # --- 触发逻辑 C: 波动率突破 (Volatility Breakout) ---
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

    def _build_ai_prompt(
        self, 
        symbol: str, 
        df: pd.DataFrame, 
        trigger_context: Dict[str, Any], 
        position: Optional[Dict[str, Any]] = None,
        is_position_check: bool = False
    ) -> str:
        """
        构建 V4 专用 Prompt (止盈止损挂单版)
        
        重大变化：
        - AI 需要返回**具体的止盈价格和止损价格**，而不是 ATR 系数
        - 新增 ADJUST action，用于调整已有持仓的止盈止损
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
        
        # 参考止盈止损价格（AI可以基于此调整）
        if trigger_context.get('signal_dir') == "LONG":
            ref_tp = current_price + (atr * 1.2)  # 参考止盈
            ref_sl = current_price - (atr * 1.5)  # 参考止损
        else:
            ref_tp = current_price - (atr * 1.2)
            ref_sl = current_price + (atr * 1.5)
            
        # 趋势判断描述
        trend_status = "顺势" if (trigger_context.get('signal_dir')=='LONG' and dist_ema50>0) or \
                                 (trigger_context.get('signal_dir')=='SHORT' and dist_ema50<0) else "逆势博弈"

        # 持仓描述
        pos_str = "当前无持仓"
        pos_side = ""
        entry_price = 0.0
        pnl_pct = 0.0
        existing_tp = None
        existing_sl = None
        
        if position and float(position.get('size', 0)) > 0:
            pos_side = position['side'].upper()
            entry_price = float(position.get('entry_price', 0))
            pnl = float(position.get('unrealized_pnl', 0))
            existing_tp = position.get('tp_price')
            existing_sl = position.get('sl_price')
            
            # 计算盈亏百分比
            if entry_price > 0:
                if pos_side == 'LONG':
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - current_price) / entry_price * 100
            
            pos_str = f"持有 {pos_side} 仓位 | 入场价: {entry_price:{price_fmt}} | 当前浮盈: {pnl_pct:+.2f}%"
            if existing_tp:
                pos_str += f" | 当前止盈单: {existing_tp:{price_fmt}}"
            if existing_sl:
                pos_str += f" | 当前止损单: {existing_sl:{price_fmt}}"

        # 根据模式构建不同的 prompt
        if is_position_check and position:
            # 持仓检查模式：评估是否需要调整止盈止损
            prompt = f"""
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
- ATR(14): {atr:{price_fmt}} (用于计算合理的止盈止损距离)
- 相对成交量(RVol): {r_vol:.2f}x
- 趋势背景: 距离 EMA50 {dist_ema50:+.2f}%
- 布林带宽: {curr['bb_width']:.4f}

【微观K线磁带 (最近10根)】
{chr(10).join(recent_candles)}

【止盈止损调整原则】
1. **盈利加速时移动止盈**: 如果价格快速向盈利方向移动，可以上移止盈，但不要太贪
2. **保护利润时移动止损**: 如果已有浮盈 >0.5%，可以将止损移动到保本位附近
3. **避免过度调整**: 如果现有止盈止损位置合理，返回 HOLD 不做调整
4. **止盈距离**: 建议 1.0-1.5 ATR
5. **止损距离**: 建议 1.2-2.0 ATR（给价格足够的呼吸空间）

【决策输出 (JSON)】
{{
    "action": "ADJUST | HOLD",
    "reason": "简要说明调整原因",
    "tp_price": {ref_tp:{price_fmt}},  // 新的止盈价格（必须是具体数字）
    "sl_price": {ref_sl:{price_fmt}}   // 新的止损价格（必须是具体数字）
}}

注意：
- 如果选择 HOLD，tp_price 和 sl_price 可以返回当前值或省略
- tp_price 和 sl_price 必须是**具体的数字**，不是系数！
- 价格精度请保持到小数点后5位
"""
        else:
            # 开仓模式：评估是否开仓并给出止盈止损价格
            prompt = f"""
身份设定：你是一名**激进的高频剥头皮交易员(Scalper)**。你的风格是"快进快出"，像鳄鱼一样捕捉猎物。

【战场态势】
- 标的: {symbol}
- 当前价格: {current_price:{price_fmt}}
- 信号方向: {trigger_context['signal_dir']}
- 触发原因: {trigger_context['trigger']}
- 趋势背景: 距离 EMA50 {dist_ema50:+.2f}% ({trend_status})
- 当前持仓: {pos_str}

【风险参考数据】
- ATR(14): {atr:{price_fmt}} (波动率基准)
- 参考止盈位: {ref_tp:{price_fmt}} (基于 1.2 ATR)
- 参考止损位: {ref_sl:{price_fmt}} (基于 1.5 ATR)
- 相对成交量(RVol): {r_vol:.2f}x

【微观K线磁带 (最近10根)】
{chr(10).join(recent_candles)}

【剥头皮决策原则】
1. **胜率优先**: 宁愿要胜率 70%、盈亏比 1:1 的单子，也不要胜率 30%、盈亏比 1:3 的单子
2. **抗噪能力**: 止损必须给价格 1.2-2.0 ATR 的呼吸空间，过窄的止损 100% 会被扫
3. **快速落袋**: 止盈设在 1.0-1.5 ATR，不贪婪
4. **动量确认**: RVol > 1.0 且价格顺势突破时，积极进场
5. **形态关键位**: 根据K线形态，止损可以放在关键支撑/阻力位外侧

【决策输出 (JSON)】
{{
    "action": "EXECUTE | REJECT", 
    "confidence": "HIGH | MEDIUM | LOW",
    "reason": "简短犀利的决策理由",
    "tp_price": {ref_tp:{price_fmt}},  // 止盈价格（必须是具体数字）
    "sl_price": {ref_sl:{price_fmt}}   // 止损价格（必须是具体数字）
}}

**重要提醒**:
- tp_price 和 sl_price 必须是**具体的价格数字**，会直接用于挂止盈止损条件单！
- 做多时: tp_price > 当前价格 > sl_price
- 做空时: sl_price > 当前价格 > tp_price
- 价格精度请保持到小数点后5位
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
        V4 执行流程 (止盈止损挂单版)
        
        核心变化：
        1. 开仓时，AI 返回具体的止盈止损价格，Executor 会同时挂条件单
        2. 有持仓时，AI 评估是否需要调整止盈止损，Executor 会撤旧挂新
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
        
        state_store = StateStore()
        curr_price = float(df.iloc[-1]['close'])
        atr = float(df.iloc[-1]['atr'])
        
        # ==== 分支A: 有持仓时 - 检查是否需要调整止盈止损 ====
        if position and float(position.get('size', 0)) > 0:
            return await self._handle_position_check(symbol, df, position, state_store)
        
        # ==== 分支B: 无持仓时 - 检查是否有开仓信号 ====
        # 3. Python 第一层筛选 (Hard Filter)
        is_triggered, reason, context = self._check_hard_triggers(df, position)
        
        if not is_triggered:
            return None
            
        logger.info(f"[{self.name}] 触发Python信号: {reason} | 准备请求AI确认...")
        
        # 记录触发事件
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
            prompt = self._build_ai_prompt(symbol, df, context, position, is_position_check=False)
            
            # 使用 asyncio.wait_for 设置超时，防止 AI API 卡住
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=settings.ai_model,
                        messages=[
                            {"role": "system", "content": "你是专业的加密货币交易风控官。只输出JSON，价格精度保持5位小数。"},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.2,
                        max_tokens=300,
                        timeout=30
                    ),
                    timeout=45
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
            
            # 获取 AI 返回的具体止盈止损价格
            tp_price = ai_decision.get('tp_price')
            sl_price = ai_decision.get('sl_price')
            
            # 记录AI决策结果
            await state_store.add_ai_event({
                "type": "result",
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "trigger": context['trigger'],
                "decision": action,
                "reason": ai_reason,
                "confidence": confidence_str,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "raw_response": ai_decision
            })

            if action != "EXECUTE":
                logger.info(f"[{self.name}] AI拒绝信号: {ai_reason}")
                return None
            
            # 验证止盈止损价格
            if tp_price is None or sl_price is None:
                logger.warning(f"[{self.name}] AI未返回有效的止盈止损价格，使用默认值")
                signal_dir = context['signal_dir']
                if signal_dir == "LONG":
                    tp_price = curr_price + (atr * 1.2)
                    sl_price = curr_price - (atr * 1.5)
                else:
                    tp_price = curr_price - (atr * 1.2)
                    sl_price = curr_price + (atr * 1.5)
            else:
                tp_price = float(tp_price)
                sl_price = float(sl_price)
                
            # 5. 构建最终信号
            signal_type = SignalType.BUY if context['signal_dir'] == "LONG" else SignalType.SELL
            
            # 映射置信度
            conf_map = {'HIGH': Confidence.HIGH, 'MEDIUM': Confidence.MEDIUM, 'LOW': Confidence.LOW}
            
            signal = Signal(
                signal_type=signal_type,
                symbol=symbol,
                confidence=conf_map.get(confidence_str, Confidence.LOW),
                reason=f"[HybridV4] Python触发: {context['trigger']} | AI确认: {ai_reason}",
                stop_loss=sl_price,
                take_profit=tp_price,
                amount=settings.trading_amount,
                strategy_name=self.name,
                weight=self.weight,
                metadata={
                    "hybrid_log": {
                        "trigger": context,
                        "ai_decision": ai_decision,
                        "indicators": {
                            "rsi": context['rsi'],
                            "atr": atr
                        }
                    },
                    # 标记需要挂止盈止损单
                    "place_tp_sl_orders": True,
                    "tp_price": tp_price,
                    "sl_price": sl_price
                }
            )
            
            logger.success(f"[{self.name}] 信号生成! {signal_type.value} @ {curr_price} | TP: {tp_price:.5f} | SL: {sl_price:.5f}")
            return signal
            
        except Exception as e:
            logger.error(f"[{self.name}] AI分析异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    async def _handle_position_check(
        self,
        symbol: str,
        df: pd.DataFrame,
        position: Dict[str, Any],
        state_store: StateStore
    ) -> Optional[Signal]:
        """
        持仓检查：评估是否需要调整止盈止损
        
        返回：
        - 如果需要调整，返回 ADJUST 类型的 Signal
        - 如果不需要调整，返回 None
        """
        try:
            pos_side = position.get('side', '').upper()
            existing_tp = position.get('tp_price')
            existing_sl = position.get('sl_price')
            
            # 如果还没有止盈止损单，需要设置
            if not existing_tp or not existing_sl:
                logger.info(f"[{self.name}] 持仓缺少止盈止损单，需要设置")
            
            # 构建持仓检查 prompt
            trigger_context = {
                "signal_dir": pos_side,
                "trigger": "POSITION_CHECK"
            }
            
            prompt = self._build_ai_prompt(
                symbol, df, trigger_context, position, is_position_check=True
            )
            
            # 使用 asyncio.wait_for 设置超时
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=settings.ai_model,
                        messages=[
                            {"role": "system", "content": "你是专业的加密货币交易风控官。只输出JSON，价格精度保持5位小数。"},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.2,
                        max_tokens=200,
                        timeout=30
                    ),
                    timeout=45
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{self.name}] 持仓检查 AI API 超时，跳过调整")
                return None
            
            result_text = response.choices[0].message.content
            
            # 检查空响应
            if not result_text or not result_text.strip():
                logger.warning(f"[{self.name}] 持仓检查 AI 返回空内容，跳过调整")
                return None
            
            # 解析 JSON
            start = result_text.find('{')
            end = result_text.rfind('}') + 1
            if start == -1 or end <= start:
                logger.warning(f"[{self.name}] 持仓检查 AI 响应中未找到有效 JSON")
                return None
            
            ai_decision = json.loads(result_text[start:end])
            
            action = ai_decision.get('action', 'HOLD').upper()
            ai_reason = ai_decision.get('reason', '')
            new_tp = ai_decision.get('tp_price')
            new_sl = ai_decision.get('sl_price')
            
            # 记录检查结果
            await state_store.add_ai_event({
                "type": "position_check",
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "action": action,
                "reason": ai_reason,
                "new_tp": new_tp,
                "new_sl": new_sl,
                "old_tp": existing_tp,
                "old_sl": existing_sl
            })
            
            if action == "HOLD":
                logger.debug(f"[{self.name}] 止盈止损无需调整: {ai_reason}")
                return None
            
            if action == "ADJUST" and new_tp and new_sl:
                new_tp = float(new_tp)
                new_sl = float(new_sl)
                
                # 检查是否真的有变化（避免频繁调整）
                tp_changed = existing_tp is None or abs(new_tp - float(existing_tp)) > 0.00001
                sl_changed = existing_sl is None or abs(new_sl - float(existing_sl)) > 0.00001
                
                if not tp_changed and not sl_changed:
                    logger.debug(f"[{self.name}] 止盈止损价格变化不大，跳过调整")
                    return None
                
                logger.info(f"[{self.name}] 需要调整止盈止损: TP {existing_tp} -> {new_tp:.5f}, SL {existing_sl} -> {new_sl:.5f}")
                
                # 构建调整信号
                signal = Signal(
                    signal_type=SignalType.HOLD,  # HOLD 类型表示不开新仓，但需要调整
                    symbol=symbol,
                    confidence=Confidence.MEDIUM,
                    reason=f"[HybridV4] 调整止盈止损: {ai_reason}",
                    stop_loss=new_sl,
                    take_profit=new_tp,
                    amount=0,  # 不交易
                    strategy_name=self.name,
                    weight=self.weight,
                    metadata={
                        "adjust_tp_sl": True,
                        "tp_price": new_tp,
                        "sl_price": new_sl,
                        "old_tp": existing_tp,
                        "old_sl": existing_sl
                    }
                )
                return signal
            
            return None
            
        except Exception as e:
            logger.error(f"[{self.name}] 持仓检查异常: {e}")
            return None
