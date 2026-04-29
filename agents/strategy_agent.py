"""
策略Agent - 可插拔策略管理
支持：
- 单策略模式：使用单一策略
- 投票模式：多策略加权投票
"""
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
from loguru import logger

from core.base_agent import BaseAgent
from core.message import Message, MessageType, Signal, SignalType, Confidence
from strategies import StrategyFactory, BaseStrategy
from indicators import TechnicalIndicators
from config import settings


class StrategyAgent(BaseAgent):
    """
    策略Agent - 管理和执行交易策略
    """
    
    def __init__(self):
        super().__init__(name="StrategyAgent")
        
        self.mode = settings.strategy_mode  # single / voting
        self.vote_threshold = settings.vote_threshold
        self.indicators = TechnicalIndicators()
        
        # 动态加载策略
        self.strategies: List[BaseStrategy] = []
        self._load_strategies()
        
        # 统计
        self.stats = {
            'total_analyses': 0,
            'signals_generated': 0,
            'buy_signals': 0,
            'sell_signals': 0,
        }
    
    def _load_strategies(self):
        """从配置加载策略"""
        strategy_names = settings.get_enabled_strategies()
        
        if not strategy_names:
            logger.warning("未配置任何策略，使用默认ai_scalping")
            strategy_names = ["ai_scalping"]
        
        self.strategies = StrategyFactory.create_multiple(strategy_names)
        
        logger.info(f"策略模式: {self.mode}")
        logger.info(f"已加载策略: {[s.name for s in self.strategies]}")
    
    def _register_handlers(self):
        self.register_handler(MessageType.MARKET_DATA, self._on_market_data)
    
    async def _on_market_data(self, message: Message):
        """处理市场数据"""
        try:
            data = message.data
            symbol = data['symbol']
            klines = data['klines']
            ticker = data['ticker']
            
            self.stats['total_analyses'] += 1
            
            if not klines or len(klines) < 10:
                await self.log("WARNING", "K线数据不足")
                return
            
            # 当前持仓
            position = None
            if symbol in self.state_store.positions:
                position = self.state_store.positions[symbol].to_dict()
            
            # 执行策略分析
            if self.mode == "single":
                final_signal = await self._single_strategy_analyze(
                    symbol, klines, ticker, position
                )
            else:
                final_signal = await self._voting_analyze(
                    symbol, klines, ticker, position
                )
            
            # 发送信号
            # 注意: HOLD 信号如果带有 adjust_tp_sl 标记，也需要发送（用于调整止盈止损）
            should_emit = final_signal and (
                final_signal.signal_type != SignalType.HOLD or 
                final_signal.metadata.get('adjust_tp_sl', False)
            )
            
            if should_emit:
                self.stats['signals_generated'] += 1
                
                if final_signal.signal_type == SignalType.BUY:
                    self.stats['buy_signals'] += 1
                elif final_signal.signal_type == SignalType.SELL:
                    self.stats['sell_signals'] += 1
                
                await self.state_store.add_signal(final_signal.to_dict())
                
                await self.emit(
                    MessageType.SIGNAL_GENERATED,
                    {
                        'signal': final_signal.to_dict(),
                        'current_price': klines[-1]['close'],
                    },
                    target="RiskAgent"
                )
                
                # 调整止盈止损用不同的日志格式
                if final_signal.metadata.get('adjust_tp_sl'):
                    tp = final_signal.metadata.get('tp_price') or final_signal.take_profit
                    sl = final_signal.metadata.get('sl_price') or final_signal.stop_loss
                    tp_text = f"{float(tp):.5f}" if tp is not None else "None"
                    sl_text = f"{float(sl):.5f}" if sl is not None else "None"
                    await self.log(
                        "INFO",
                        f"🔧 调整TP/SL: TP={tp_text}, SL={sl_text}"
                    )
                else:
                    await self.log(
                        "INFO",
                        f"📊 信号: {final_signal.signal_type.value} | "
                        f"{final_signal.confidence.value} | {final_signal.reason[:40]}"
                    )
                
        except Exception as e:
            await self.log("ERROR", f"策略分析失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
    
    async def _single_strategy_analyze(
        self,
        symbol: str,
        klines: List[Dict],
        ticker: Dict,
        position: Optional[Dict]
    ) -> Optional[Signal]:
        """单策略模式"""
        if not self.strategies:
            return None
        
        strategy = self.strategies[0]
        
        try:
            signal = await strategy.analyze(symbol, klines, ticker, position)
            return signal
        except Exception as e:
            await self.log("ERROR", f"策略{strategy.name}执行失败: {e}")
            return None
    
    async def _voting_analyze(
        self,
        symbol: str,
        klines: List[Dict],
        ticker: Dict,
        position: Optional[Dict]
    ) -> Optional[Signal]:
        """投票模式"""
        signals: List[Signal] = []
        
        # 并行执行所有策略
        tasks = []
        for strategy in self.strategies:
            if strategy.enabled:
                tasks.append(self._get_signal(strategy, symbol, klines, ticker, position))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Signal):
                signals.append(result)
        
        if not signals:
            return None
        
        return self._aggregate_signals(signals, symbol)
    
    async def _get_signal(
        self,
        strategy: BaseStrategy,
        symbol: str,
        klines: List[Dict],
        ticker: Dict,
        position: Optional[Dict]
    ) -> Optional[Signal]:
        """获取单个策略信号"""
        try:
            return await strategy.analyze(symbol, klines, ticker, position)
        except Exception as e:
            logger.error(f"策略{strategy.name}异常: {e}")
            return None
    
    def _aggregate_signals(
        self,
        signals: List[Signal],
        symbol: str
    ) -> Optional[Signal]:
        """信号投票聚合"""
        if not signals:
            return None
        
        # 权重统计
        weights = {'buy': 0.0, 'sell': 0.0, 'hold': 0.0, 'close': 0.0}
        reasons = {'buy': [], 'sell': [], 'close': []}
        
        confidence_mult = {
            Confidence.HIGH: 1.0,
            Confidence.MEDIUM: 0.7,
            Confidence.LOW: 0.4
        }
        
        for sig in signals:
            w = sig.weight * confidence_mult.get(sig.confidence, 0.5)
            
            if sig.signal_type == SignalType.BUY:
                weights['buy'] += w
                reasons['buy'].append(sig.reason)
            elif sig.signal_type == SignalType.SELL:
                weights['sell'] += w
                reasons['sell'].append(sig.reason)
            elif sig.signal_type in [SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT]:
                weights['close'] += w
                reasons['close'].append(sig.reason)
            else:
                weights['hold'] += w
        
        total = sum(weights.values())
        if total == 0:
            return None
        
        # 确定方向
        threshold = self.vote_threshold
        
        if weights['close'] / total > threshold:
            # 平仓信号优先
            signal_type = SignalType.CLOSE_LONG  # 会在executor中判断实际方向
            reason_list = reasons['close']
        elif weights['buy'] / total > threshold and weights['buy'] > weights['sell']:
            signal_type = SignalType.BUY
            reason_list = reasons['buy']
        elif weights['sell'] / total > threshold and weights['sell'] > weights['buy']:
            signal_type = SignalType.SELL
            reason_list = reasons['sell']
        else:
            return Signal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                confidence=Confidence.LOW,
                reason="投票未达阈值",
                strategy_name="Voting"
            )
        
        # 置信度
        score = max(weights['buy'], weights['sell'], weights['close']) / total
        confidence = Confidence.HIGH if score >= 0.7 else Confidence.MEDIUM if score >= 0.5 else Confidence.LOW
        
        # 止损止盈取平均
        sl_list = [s.stop_loss for s in signals if s.stop_loss and s.signal_type == signal_type]
        tp_list = [s.take_profit for s in signals if s.take_profit and s.signal_type == signal_type]
        
        return Signal(
            signal_type=signal_type,
            symbol=symbol,
            confidence=confidence,
            reason=" | ".join(reason_list[:2]),
            stop_loss=sum(sl_list)/len(sl_list) if sl_list else None,
            take_profit=sum(tp_list)/len(tp_list) if tp_list else None,
            amount=settings.trading_amount,
            strategy_name="Voting",
            weight=1.0,
            metadata={'weights': weights}
        )
    
    async def handle_message(self, message: Message):
        if message.msg_type == MessageType.SYSTEM_STOP:
            await self.stop()
    
    def get_stats(self) -> Dict[str, Any]:
        return self.stats
