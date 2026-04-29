"""技术指标计算"""
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class IndicatorResult:
    """指标计算结果"""
    name: str
    value: float
    signal: str  # 'bullish', 'bearish', 'neutral'
    strength: float  # 0-1
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'value': self.value,
            'signal': self.signal,
            'strength': self.strength
        }


class TechnicalIndicators:
    """
    技术指标计算器
    支持常用技术指标的计算
    """
    
    @staticmethod
    def calculate_sma(prices: List[float], period: int) -> Optional[float]:
        """简单移动平均线"""
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period
    
    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> Optional[float]:
        """指数移动平均线"""
        if len(prices) < period:
            return None
        
        df = pd.Series(prices)
        ema = df.ewm(span=period, adjust=False).mean()
        return float(ema.iloc[-1])
    
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> Optional[IndicatorResult]:
        """
        相对强弱指标 (RSI)
        RSI > 70: 超买
        RSI < 30: 超卖
        """
        if len(prices) < period + 1:
            return None
        
        df = pd.Series(prices)
        delta = df.diff()
        
        gain = delta.where(delta > 0, 0)
        loss = (-delta).where(delta < 0, 0)
        
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        rsi_value = float(rsi.iloc[-1])
        
        # 判断信号
        if rsi_value > 70:
            signal = 'bearish'
            strength = min((rsi_value - 70) / 30, 1.0)
        elif rsi_value < 30:
            signal = 'bullish'
            strength = min((30 - rsi_value) / 30, 1.0)
        else:
            signal = 'neutral'
            strength = 0.0
        
        return IndicatorResult(
            name='RSI',
            value=rsi_value,
            signal=signal,
            strength=strength
        )
    
    @staticmethod
    def calculate_macd(
        prices: List[float],
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> Optional[Dict[str, Any]]:
        """
        MACD指标
        返回: MACD线、信号线、柱状图
        """
        if len(prices) < slow_period + signal_period:
            return None
        
        df = pd.Series(prices)
        
        # 计算EMA
        ema_fast = df.ewm(span=fast_period, adjust=False).mean()
        ema_slow = df.ewm(span=slow_period, adjust=False).mean()
        
        # MACD线
        macd_line = ema_fast - ema_slow
        
        # 信号线
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
        
        # 柱状图
        histogram = macd_line - signal_line
        
        macd_val = float(macd_line.iloc[-1])
        signal_val = float(signal_line.iloc[-1])
        hist_val = float(histogram.iloc[-1])
        
        # 判断信号
        if hist_val > 0 and macd_val > signal_val:
            signal = 'bullish'
            strength = min(abs(hist_val) / abs(macd_val) if macd_val != 0 else 0, 1.0)
        elif hist_val < 0 and macd_val < signal_val:
            signal = 'bearish'
            strength = min(abs(hist_val) / abs(macd_val) if macd_val != 0 else 0, 1.0)
        else:
            signal = 'neutral'
            strength = 0.0
        
        return {
            'macd': IndicatorResult('MACD', macd_val, signal, strength),
            'signal': signal_val,
            'histogram': hist_val
        }
    
    @staticmethod
    def calculate_bollinger_bands(
        prices: List[float],
        period: int = 20,
        std_dev: float = 2.0
    ) -> Optional[Dict[str, Any]]:
        """
        布林带
        返回: 上轨、中轨、下轨、%B
        """
        if len(prices) < period:
            return None
        
        df = pd.Series(prices)
        
        middle = df.rolling(window=period).mean()
        std = df.rolling(window=period).std()
        
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        
        current_price = prices[-1]
        upper_val = float(upper.iloc[-1])
        middle_val = float(middle.iloc[-1])
        lower_val = float(lower.iloc[-1])
        
        # %B 指标
        percent_b = (current_price - lower_val) / (upper_val - lower_val) if (upper_val - lower_val) != 0 else 0.5
        
        # 判断信号
        if percent_b > 1:
            signal = 'bearish'  # 突破上轨，可能回调
            strength = min(percent_b - 1, 1.0)
        elif percent_b < 0:
            signal = 'bullish'  # 突破下轨，可能反弹
            strength = min(abs(percent_b), 1.0)
        else:
            signal = 'neutral'
            strength = 0.0
        
        return {
            'upper': upper_val,
            'middle': middle_val,
            'lower': lower_val,
            'percent_b': IndicatorResult('BB%B', percent_b, signal, strength)
        }
    
    @staticmethod
    def calculate_atr(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14
    ) -> Optional[float]:
        """
        平均真实波幅 (ATR)
        用于计算止损位置
        """
        if len(closes) < period + 1:
            return None
        
        df = pd.DataFrame({
            'high': highs,
            'low': lows,
            'close': closes
        })
        
        # 计算真实波幅
        df['prev_close'] = df['close'].shift(1)
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = abs(df['high'] - df['prev_close'])
        df['tr3'] = abs(df['low'] - df['prev_close'])
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        
        # ATR
        atr = df['tr'].rolling(window=period).mean()
        
        return float(atr.iloc[-1])
    
    @staticmethod
    def calculate_trend(prices: List[float], period: int = 20) -> Optional[IndicatorResult]:
        """
        趋势判断
        基于EMA斜率判断趋势强度
        """
        if len(prices) < period:
            return None
        
        df = pd.Series(prices)
        ema = df.ewm(span=period, adjust=False).mean()
        
        # 计算斜率（最近5个点的线性回归斜率）
        recent_ema = ema.tail(5).values
        x = np.arange(len(recent_ema))
        slope = np.polyfit(x, recent_ema, 1)[0]
        
        # 归一化斜率
        price_range = max(prices[-period:]) - min(prices[-period:])
        normalized_slope = slope / (price_range / period) if price_range > 0 else 0
        
        # 判断趋势
        if normalized_slope > 0.1:
            signal = 'bullish'
            strength = min(normalized_slope, 1.0)
        elif normalized_slope < -0.1:
            signal = 'bearish'
            strength = min(abs(normalized_slope), 1.0)
        else:
            signal = 'neutral'
            strength = 0.0
        
        return IndicatorResult(
            name='Trend',
            value=normalized_slope,
            signal=signal,
            strength=strength
        )
    
    @classmethod
    def analyze_all(
        cls,
        klines: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        计算所有指标并汇总
        """
        if not klines or len(klines) < 30:
            return {'error': '数据不足'}
        
        # 提取价格数据
        closes = [k['close'] for k in klines]
        highs = [k['high'] for k in klines]
        lows = [k['low'] for k in klines]
        
        result = {
            'price': closes[-1],
            'indicators': {},
            'summary': {
                'bullish_count': 0,
                'bearish_count': 0,
                'neutral_count': 0,
                'overall_signal': 'neutral',
                'confidence': 0.0
            }
        }
        
        # RSI
        rsi = cls.calculate_rsi(closes)
        if rsi:
            result['indicators']['rsi'] = rsi.to_dict()
            if rsi.signal == 'bullish':
                result['summary']['bullish_count'] += 1
            elif rsi.signal == 'bearish':
                result['summary']['bearish_count'] += 1
            else:
                result['summary']['neutral_count'] += 1
        
        # MACD
        macd = cls.calculate_macd(closes)
        if macd:
            result['indicators']['macd'] = macd['macd'].to_dict()
            result['indicators']['macd']['signal_line'] = macd['signal']
            result['indicators']['macd']['histogram'] = macd['histogram']
            if macd['macd'].signal == 'bullish':
                result['summary']['bullish_count'] += 1
            elif macd['macd'].signal == 'bearish':
                result['summary']['bearish_count'] += 1
            else:
                result['summary']['neutral_count'] += 1
        
        # 布林带
        bb = cls.calculate_bollinger_bands(closes)
        if bb:
            result['indicators']['bollinger'] = {
                'upper': bb['upper'],
                'middle': bb['middle'],
                'lower': bb['lower'],
                **bb['percent_b'].to_dict()
            }
            if bb['percent_b'].signal == 'bullish':
                result['summary']['bullish_count'] += 1
            elif bb['percent_b'].signal == 'bearish':
                result['summary']['bearish_count'] += 1
            else:
                result['summary']['neutral_count'] += 1
        
        # 趋势
        trend = cls.calculate_trend(closes)
        if trend:
            result['indicators']['trend'] = trend.to_dict()
            if trend.signal == 'bullish':
                result['summary']['bullish_count'] += 1
            elif trend.signal == 'bearish':
                result['summary']['bearish_count'] += 1
            else:
                result['summary']['neutral_count'] += 1
        
        # ATR
        atr = cls.calculate_atr(highs, lows, closes)
        if atr:
            result['indicators']['atr'] = atr
        
        # 综合信号
        bullish = result['summary']['bullish_count']
        bearish = result['summary']['bearish_count']
        total = bullish + bearish + result['summary']['neutral_count']
        
        if total > 0:
            if bullish > bearish:
                result['summary']['overall_signal'] = 'bullish'
                result['summary']['confidence'] = bullish / total
            elif bearish > bullish:
                result['summary']['overall_signal'] = 'bearish'
                result['summary']['confidence'] = bearish / total
            else:
                result['summary']['overall_signal'] = 'neutral'
                result['summary']['confidence'] = 0.5
        
        return result
