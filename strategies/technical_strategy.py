"""技术指标策略"""
from typing import Dict, Any, Optional, List
from loguru import logger

from .base_strategy import BaseStrategy
from core.message import Signal, SignalType, Confidence
from indicators import TechnicalIndicators


class TechnicalStrategy(BaseStrategy):
    """
    技术指标策略
    基于RSI、MACD、布林带等指标综合判断
    """
    
    def __init__(self, weight: float = 0.3):
        super().__init__(name="TechnicalStrategy", weight=weight)
        self.indicators = TechnicalIndicators()
    
    async def analyze(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]] = None
    ) -> Optional[Signal]:
        """基于技术指标分析"""
        
        if not self.enabled:
            return None
        
        if not klines or len(klines) < 30:
            logger.warning("K线数据不足，无法计算技术指标")
            return None
        
        # 计算所有指标
        analysis = self.indicators.analyze_all(klines)
        
        if 'error' in analysis:
            logger.warning(f"指标计算错误: {analysis['error']}")
            return None
        
        summary = analysis['summary']
        indicators = analysis['indicators']
        current_price = analysis['price']
        
        # 生成信号
        overall_signal = summary['overall_signal']
        confidence_score = summary['confidence']
        
        # 确定信号类型
        if overall_signal == 'bullish' and confidence_score >= 0.6:
            signal_type = SignalType.BUY
        elif overall_signal == 'bearish' and confidence_score >= 0.6:
            signal_type = SignalType.SELL
        else:
            signal_type = SignalType.HOLD
        
        # 确定置信度
        if confidence_score >= 0.75:
            confidence = Confidence.HIGH
        elif confidence_score >= 0.5:
            confidence = Confidence.MEDIUM
        else:
            confidence = Confidence.LOW
        
        # 计算止损止盈
        atr = indicators.get('atr', current_price * 0.02)
        
        if signal_type == SignalType.BUY:
            stop_loss = current_price - (atr * 2)
            take_profit = current_price + (atr * 3)
        elif signal_type == SignalType.SELL:
            stop_loss = current_price + (atr * 2)
            take_profit = current_price - (atr * 3)
        else:
            stop_loss = None
            take_profit = None
        
        # 生成分析理由
        reasons = []
        if 'rsi' in indicators:
            rsi = indicators['rsi']
            reasons.append(f"RSI={rsi['value']:.1f}({rsi['signal']})")
        if 'macd' in indicators:
            macd = indicators['macd']
            reasons.append(f"MACD={macd['signal']}")
        if 'trend' in indicators:
            trend = indicators['trend']
            reasons.append(f"趋势={trend['signal']}")
        
        reason = ', '.join(reasons)
        
        signal = Signal(
            signal_type=signal_type,
            symbol=symbol,
            confidence=confidence,
            reason=f"技术指标: {reason}",
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy_name=self.name,
            weight=self.weight,
            metadata={'indicators': analysis}
        )
        
        logger.info(f"[{self.name}] 信号: {signal_type.value} 置信度: {confidence.value}")
        
        return signal
