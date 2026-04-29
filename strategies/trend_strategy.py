"""趋势跟踪策略"""
from typing import Dict, Any, Optional, List
from loguru import logger

from .base_strategy import BaseStrategy
from core.message import Signal, SignalType, Confidence
from indicators import TechnicalIndicators


class TrendStrategy(BaseStrategy):
    """
    趋势跟踪策略
    基于多周期EMA和趋势强度判断
    """
    
    def __init__(self, weight: float = 0.3):
        super().__init__(name="TrendStrategy", weight=weight)
        self.ema_periods = [5, 10, 20, 50]
    
    async def analyze(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]] = None
    ) -> Optional[Signal]:
        """趋势分析"""
        
        if not self.enabled:
            return None
        
        if not klines or len(klines) < 50:
            logger.warning("K线数据不足，无法进行趋势分析")
            return None
        
        closes = [k['close'] for k in klines]
        current_price = closes[-1]
        
        # 计算多周期EMA
        emas = {}
        for period in self.ema_periods:
            ema = TechnicalIndicators.calculate_ema(closes, period)
            if ema:
                emas[period] = ema
        
        if len(emas) < 4:
            return None
        
        # 判断趋势
        ema5 = emas[5]
        ema10 = emas[10]
        ema20 = emas[20]
        ema50 = emas[50]
        
        # 多头排列: EMA5 > EMA10 > EMA20 > EMA50
        bullish_alignment = ema5 > ema10 > ema20 > ema50
        
        # 空头排列: EMA5 < EMA10 < EMA20 < EMA50
        bearish_alignment = ema5 < ema10 < ema20 < ema50
        
        # 价格与均线的关系
        price_above_ema20 = current_price > ema20
        price_below_ema20 = current_price < ema20
        
        # 计算趋势强度
        trend_result = TechnicalIndicators.calculate_trend(closes)
        trend_strength = trend_result.strength if trend_result else 0
        
        # 确定信号
        if bullish_alignment and price_above_ema20 and trend_strength > 0.3:
            signal_type = SignalType.BUY
            reason = f"多头趋势: EMA排列向上, 价格在EMA20({ema20:.2f})之上"
        elif bearish_alignment and price_below_ema20 and trend_strength > 0.3:
            signal_type = SignalType.SELL
            reason = f"空头趋势: EMA排列向下, 价格在EMA20({ema20:.2f})之下"
        else:
            signal_type = SignalType.HOLD
            reason = "趋势不明确，建议观望"
        
        # 置信度
        if trend_strength >= 0.6:
            confidence = Confidence.HIGH
        elif trend_strength >= 0.3:
            confidence = Confidence.MEDIUM
        else:
            confidence = Confidence.LOW
        
        # 止损止盈
        atr = TechnicalIndicators.calculate_atr(
            [k['high'] for k in klines],
            [k['low'] for k in klines],
            closes
        ) or current_price * 0.02
        
        if signal_type == SignalType.BUY:
            stop_loss = min(ema20, current_price - atr * 2)
            take_profit = current_price + atr * 4
        elif signal_type == SignalType.SELL:
            stop_loss = max(ema20, current_price + atr * 2)
            take_profit = current_price - atr * 4
        else:
            stop_loss = None
            take_profit = None
        
        signal = Signal(
            signal_type=signal_type,
            symbol=symbol,
            confidence=confidence,
            reason=reason,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy_name=self.name,
            weight=self.weight,
            metadata={
                'emas': emas,
                'trend_strength': trend_strength,
                'bullish_alignment': bullish_alignment,
                'bearish_alignment': bearish_alignment
            }
        )
        
        logger.info(f"[{self.name}] 信号: {signal_type.value} 趋势强度: {trend_strength:.2f}")
        
        return signal
