"""
AI剥头皮策略 - 专为短线频繁交易设计
利用AI分析微观市场结构，快进快出获取小利润

策略原理：
1. AI分析最近K线的微观结构（价格行为、成交量、波动率）
2. 识别短期超买超卖和动量变化
3. 快速入场，小止盈(0.3-0.5%)，小止损(0.5%)
4. 严格的时间止损（持仓不超过N分钟）

适用场景：高波动币种如DOGE、SHIB等
"""
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
import pandas as pd
import numpy as np
from openai import AsyncOpenAI
from loguru import logger

from .base_strategy import BaseStrategy
from core.message import Signal, SignalType, Confidence
from config import settings


class AIScalpingStrategy(BaseStrategy):
    """
    AI剥头皮策略
    特点：
    1. 使用AI分析微观市场结构
    2. 快进快出，追求高胜率小利润
    3. 严格风控，快速止损
    """
    
    def __init__(self, weight: float = 1.0):
        super().__init__(name="AIScalpingStrategy", weight=weight)
        
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )
        
        # 策略参数（从配置读取）
        config = settings.get_strategy_config("ai_scalping")
        self.min_profit = config.get("min_profit", 0.3)  # 最小目标利润%
        self.max_loss = config.get("max_loss", 0.5)      # 最大止损%
        self.hold_minutes = config.get("hold_minutes", 10)
        
        # 交易记录
        self.last_signal_time: Optional[datetime] = None
        self.signal_cooldown = 0  # 移除冷却时间，提高频率
    
    def _calculate_indicators(self, klines: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算技术指标"""
        try:
            df = pd.DataFrame(klines)
            df['close'] = df['close'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['volume'] = df['volume'].astype(float)
            
            # RSI (Relative Strength Index)
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # MACD
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['hist'] = df['macd'] - df['signal']
            
            # Bollinger Bands
            df['ma20'] = df['close'].rolling(window=20).mean()
            df['std'] = df['close'].rolling(window=20).std()
            df['upper_bb'] = df['ma20'] + (df['std'] * 2)
            df['lower_bb'] = df['ma20'] - (df['std'] * 2)
            
            # EMA Trend
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            
            # ATR (Average True Range) for Stops
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            df['atr'] = true_range.rolling(14).mean()

            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            return {
                "rsi": round(latest['rsi'], 2),
                "macd": round(latest['macd'], 6),
                "macd_hist": round(latest['hist'], 6),
                "macd_prev_hist": round(prev['hist'], 6),
                "upper_bb": round(latest['upper_bb'], 4),
                "lower_bb": round(latest['lower_bb'], 4),
                "ma20": round(latest['ma20'], 4),
                "ema50": round(latest['ema50'], 4),
                "atr": round(latest['atr'], 4),
                "trend": "BULLISH" if latest['close'] > latest['ema50'] else "BEARISH",
                "volatility_rank": "HIGH" if latest['std'] > df['std'].mean() else "LOW"
            }
        except Exception as e:
            logger.error(f"指标计算失败: {e}")
            return {}

    def _build_scalping_prompt(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        position: Optional[Dict[str, Any]]
    ) -> str:
        """构建剥头皮分析Prompt"""
        
        # 计算指标
        tech = self._calculate_indicators(klines)
        
        # 基础数据
        current_price = klines[-1]['close']
        
        # 价格变化序列 (最近5根)
        recent_closes = [k['close'] for k in klines[-6:]]
        price_action = []
        for i in range(1, len(recent_closes)):
            chg = (recent_closes[i] - recent_closes[i-1]) / recent_closes[i-1] * 100
            price_action.append(f"{chg:+.2f}%")
            
        # 资金费率/成交量 (模拟数据，如果有真实数据更好)
        vol_current = klines[-1]['volume']
        vol_avg = sum(k['volume'] for k in klines[-20:]) / 20
        vol_ratio = vol_current / vol_avg if vol_avg > 0 else 0
        
        # 持仓描述
        pos_str = "无持仓 (空仓待命)"
        if position:
            side = position['side']
            entry = position['entry_price']
            pnl = (current_price - entry) / entry * 100 if side == 'long' else (entry - current_price) / entry * 100
            pos_str = f"持有 {side.upper()} 仓位 | 入场: {entry} | 当前浮盈: {pnl:+.2f}%"

        prompt = f"""
你是一个顶级高频量化交易员(IQ 160)，擅长剥头皮策略(Scalping)。你的任务是利用微观市场结构和技术指标捕捉短线利润(0.3%-1.0%)。

【当前市场状态 - {symbol}】
1. 价格行为:
   - 当前价格: {current_price}
   - 最近5根K线走势: {' -> '.join(price_action)}
   - 成交量倍数: {vol_ratio:.2f}x (大于1.5意味着高活跃)

2. 关键技术指标:
   - 趋势(EMA50): {tech.get('trend')} (当前价格 vs EMA50: {tech.get('ema50')})
   - 动量(RSI 14): {tech.get('rsi')} (30=超卖, 70=超买)
   - MACD柱状图: {tech.get('macd_hist')} (正值增强, 负值减弱)
   - 布林带: 上轨{tech.get('upper_bb')} | 下轨{tech.get('lower_bb')}
   - ATR(波动): {tech.get('atr')}

3. 账户持仓:
   {pos_str}

【决策逻辑 - 必须严格执行】
1. **开仓条件 (Aggressive but Logical)**:
   - **做多(BUY)**: 
     A) 趋势回踩: 上升趋势中回踩EMA20或布林中轨，且RSI < 50回升。
     B) 超卖反弹: 价格触及布林下轨 + RSI < 30 + 出现阳线。
     C) 突破确认: 强力突破布林上轨 + 巨量(Vol > 2x)。
   - **做空(SELL)**: 
     A) 趋势受阻: 下跌趋势中反弹EMA20或布林中轨，且RSI > 50回落。
     B) 超买回调: 价格触及布林上轨 + RSI > 70 + 出现阴线。
     C) 跌破确认: 强力跌破布林下轨 + 巨量。

2. **平仓条件**:
   - 达到止盈目标(ATR的1.5倍或固定%)。
   - 趋势反转(MACD死叉/金叉，或跌破关键均线)。
   - 止损触发。

3. **高频交易原则**:
   - 不要过度犹豫。如果符合上述任何一种微观形态，立即开仓。
   - 即使信号只有60%信心，在剥头皮策略中也值得尝试(用小止损换博弈)。
   - 只有在市场完全横盘且无波动(ATR极低)时才选择HOLD。

【输出要求】
返回严格JSON格式:
{{
    "action": "BUY | SELL | HOLD | CLOSE",
    "confidence": "HIGH | MEDIUM | LOW",
    "reason": "格式: [策略] + [信号依据]. 例如: '超卖反弹策略: 价格触及布林下轨且RSI(28)背离，成交量放大2倍'",
    "entry_price": {current_price},
    "stop_loss": (基于ATR计算的止损价),
    "take_profit": (基于ATR计算的止盈价)
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
        """执行AI剥头皮分析"""
        
        if not self.enabled:
            return None
        
        if not klines or len(klines) < 50:
            logger.warning(f"[{self.name}] K线数据不足(需50+): {len(klines) if klines else 0}")
            return None
        
        # 信号冷却检查
        if self.last_signal_time:
            elapsed = (datetime.now() - self.last_signal_time).total_seconds()
            if elapsed < self.signal_cooldown:
                return None
        
        current_price = klines[-1]['close']
        
        try:
            prompt = self._build_scalping_prompt(symbol, klines, position)
            
            response = await self.client.chat.completions.create(
                model=settings.ai_model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是专业的加密货币剥头皮交易员，专注于短线快速交易。只输出JSON格式。"
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=settings.ai_temperature,
                max_tokens=300
            )
            
            result_text = response.choices[0].message.content
            
            # 解析JSON
            start = result_text.find('{')
            end = result_text.rfind('}') + 1
            if start == -1 or end == 0:
                logger.error(f"[{self.name}] AI返回格式错误")
                return None
            
            data = json.loads(result_text[start:end])
            
            # 转换信号
            action = data.get('action', 'HOLD').upper()
            confidence_str = data.get('confidence', 'LOW').upper()
            
            # 映射信号类型
            signal_map = {
                'BUY': SignalType.BUY,
                'SELL': SignalType.SELL,
                'HOLD': SignalType.HOLD,
                'CLOSE': SignalType.CLOSE_LONG if position and position.get('side') == 'long' else SignalType.CLOSE_SHORT
            }
            
            confidence_map = {
                'HIGH': Confidence.HIGH,
                'MEDIUM': Confidence.MEDIUM,
                'LOW': Confidence.LOW
            }
            
            signal_type = signal_map.get(action, SignalType.HOLD)
            confidence = confidence_map.get(confidence_str, Confidence.LOW)
            
            # 计算止损止盈
            stop_loss = data.get('stop_loss')
            take_profit = data.get('take_profit')
            
            if signal_type == SignalType.BUY and not stop_loss:
                stop_loss = current_price * (1 - self.max_loss / 100)
                take_profit = current_price * (1 + self.min_profit / 100)
            elif signal_type == SignalType.SELL and not stop_loss:
                stop_loss = current_price * (1 + self.max_loss / 100)
                take_profit = current_price * (1 - self.min_profit / 100)
            
            # 更新信号时间
            if signal_type != SignalType.HOLD:
                self.last_signal_time = datetime.now()
            
            signal = Signal(
                signal_type=signal_type,
                symbol=symbol,
                confidence=confidence,
                reason=data.get('reason', 'AI剥头皮信号'),
                stop_loss=float(stop_loss) if stop_loss else None,
                take_profit=float(take_profit) if take_profit else None,
                amount=settings.trading_amount,
                strategy_name=self.name,
                weight=self.weight,
                metadata={
                    'ai_response': data,
                    'current_price': current_price
                }
            )
            
            logger.info(f"[{self.name}] {action} | {confidence_str} | {data.get('reason', '')}")
            
            return signal
            
        except json.JSONDecodeError as e:
            logger.error(f"[{self.name}] JSON解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[{self.name}] 分析异常: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None
