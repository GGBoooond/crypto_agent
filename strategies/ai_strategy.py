"""AI分析策略 - 使用 LLMClient 调用任意 OpenAI 协议兼容模型"""
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from loguru import logger

from .base_strategy import BaseStrategy
from .llm_client import LLMClient
from core.message import Signal, SignalType, Confidence
from config import settings


class AIStrategy(BaseStrategy):
    """
    AI分析策略
    使用 LLMClient 调用配置的大模型分析市场数据
    """
    
    def __init__(self, weight: float = 0.4):
        super().__init__(name="AIStrategy", weight=weight)

        self.llm_client = LLMClient()
        self.client = self.llm_client.async_client

        self.analysis_history: List[Dict] = []
        
    def _build_prompt(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        indicators: Dict[str, Any]
    ) -> str:
        """构建分析Prompt"""
        
        # 构建K线描述
        kline_text = f"【最近K线数据 ({settings.trading_timeframe})】\n"
        for i, k in enumerate(klines[-5:], 1):
            trend = "阳线" if k['close'] > k['open'] else "阴线"
            change = ((k['close'] - k['open']) / k['open']) * 100
            kline_text += f"K{i}: {trend} O:{k['open']:.2f} H:{k['high']:.2f} L:{k['low']:.2f} C:{k['close']:.2f} 涨跌:{change:+.2f}%\n"
        
        # 技术指标文本
        indicator_text = "【技术指标】\n"
        if 'indicators' in indicators:
            ind = indicators['indicators']
            if 'rsi' in ind:
                indicator_text += f"RSI({14}): {ind['rsi']['value']:.2f} 信号:{ind['rsi']['signal']}\n"
            if 'macd' in ind:
                indicator_text += f"MACD: {ind['macd']['value']:.4f} 柱状图:{ind['macd'].get('histogram', 0):.4f}\n"
            if 'bollinger' in ind:
                bb = ind['bollinger']
                indicator_text += f"布林带: 上轨{bb['upper']:.2f} 中轨{bb['middle']:.2f} 下轨{bb['lower']:.2f}\n"
            if 'trend' in ind:
                indicator_text += f"趋势: {ind['trend']['signal']} 强度:{ind['trend']['strength']:.2f}\n"
        
        # 持仓信息
        if position:
            position_text = f"{position['side']}仓 数量:{position['size']} 入场价:{position['entry_price']:.2f} 浮动盈亏:{position.get('unrealized_pnl', 0):.2f}USDT"
        else:
            position_text = "无持仓"
        
        # 历史分析参考
        history_text = ""
        if self.analysis_history:
            last = self.analysis_history[-1]
            history_text = f"\n【上次分析】\n信号: {last.get('signal', 'N/A')} 理由: {last.get('reason', 'N/A')[:50]}..."
        
        current_price = klines[-1]['close'] if klines else 0
        
        prompt = f"""
作为专业加密货币分析师，请分析以下{symbol}市场数据：

{kline_text}

{indicator_text}

【当前行情】
- 价格: ${current_price:,.2f}
- 24h最高: ${market_data.get('high', current_price):,.2f}
- 24h最低: ${market_data.get('low', current_price):,.2f}
- 24h成交量变化: {market_data.get('change', 0):+.2f}%
- 当前持仓: {position_text}
{history_text}

【分析要求】
1. 综合技术指标和K线形态判断趋势
2. 给出明确的交易信号: BUY(做多) / SELL(做空) / HOLD(观望)
3. 如果有持仓，评估是否需要平仓
4. 给出合理的止损价和止盈价
5. 评估信心程度

请用以下JSON格式回复：
{{
    "signal": "BUY|SELL|HOLD",
    "reason": "分析理由（100字以内）",
    "stop_loss": 止损价格数字,
    "take_profit": 止盈价格数字,
    "confidence": "HIGH|MEDIUM|LOW"
}}
"""
        return prompt
    
    async def analyze(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]] = None,
        indicators: Dict[str, Any] = None
    ) -> Optional[Signal]:
        """使用AI分析市场"""
        
        if not self.enabled:
            return None
        
        if not klines or len(klines) < 5:
            logger.warning("K线数据不足，无法进行AI分析")
            return None
        
        indicators = indicators or {}
        
        try:
            prompt = self._build_prompt(symbol, klines, market_data, position, indicators)
            
            response = await self.llm_client.chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": f"你是一位专业的加密货币交易分析师，专注于{settings.trading_timeframe}周期的技术分析。请基于数据做出客观判断，避免情绪化决策。"
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=500
            )
            
            result_text = response.choices[0].message.content
            
            # 解析JSON
            start_idx = result_text.find('{')
            end_idx = result_text.rfind('}') + 1
            
            if start_idx == -1 or end_idx == 0:
                logger.error(f"AI返回格式错误: {result_text[:100]}")
                return None
            
            json_str = result_text[start_idx:end_idx]
            data = json.loads(json_str)
            
            # 记录历史
            self.analysis_history.append(data)
            if len(self.analysis_history) > 20:
                self.analysis_history.pop(0)
            
            # 转换信号类型
            signal_map = {
                'BUY': SignalType.BUY,
                'SELL': SignalType.SELL,
                'HOLD': SignalType.HOLD
            }
            
            confidence_map = {
                'HIGH': Confidence.HIGH,
                'MEDIUM': Confidence.MEDIUM,
                'LOW': Confidence.LOW
            }
            
            signal_type = signal_map.get(data.get('signal', 'HOLD'), SignalType.HOLD)
            confidence = confidence_map.get(data.get('confidence', 'LOW'), Confidence.LOW)
            
            signal = Signal(
                signal_type=signal_type,
                symbol=symbol,
                confidence=confidence,
                reason=data.get('reason', ''),
                stop_loss=float(data.get('stop_loss', 0)) if data.get('stop_loss') else None,
                take_profit=float(data.get('take_profit', 0)) if data.get('take_profit') else None,
                strategy_name=self.name,
                weight=self.weight,
                metadata={'raw_response': data}
            )
            
            logger.info(f"[{self.name}] 信号: {signal_type.value} 置信度: {confidence.value}")
            
            return signal
            
        except json.JSONDecodeError as e:
            logger.error(f"AI响应JSON解析失败: {e}")
            return None
        except Exception as e:
            import traceback
            logger.error(f"AI分析失败: {e}")
            logger.error(f"详细错误堆栈: {traceback.format_exc()}")
            return None
