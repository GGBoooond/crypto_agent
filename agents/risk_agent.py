"""风控Agent - 负责风险评估和控制"""
import asyncio
from typing import Optional
from datetime import datetime
from loguru import logger

from core.base_agent import BaseAgent
from core.message import Message, MessageType, Signal
from core.state_store import Position
from risk import RiskManager, RiskDecision
from config import RiskConfig


class RiskAgent(BaseAgent):
    """
    风控Agent
    负责评估交易信号的风险，决定是否执行
    """
    
    def __init__(self):
        super().__init__(name="RiskAgent")
        self.risk_manager = RiskManager(RiskConfig())
        self._monitor_task: Optional[asyncio.Task] = None
    
    def _register_handlers(self):
        """注册消息处理器"""
        self.register_handler(MessageType.SIGNAL_GENERATED, self._on_signal)
        self.register_handler(MessageType.MARKET_DATA, self._on_market_data)
    
    async def on_start(self):
        """启动持仓监控任务"""
        self._monitor_task = asyncio.create_task(self._position_monitor_loop())
        await self.log("INFO", "风控Agent已启动")
    
    async def on_stop(self):
        """停止监控任务"""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
    
    async def _on_signal(self, message: Message):
        """处理交易信号"""
        try:
            data = message.data
            signal_data = data['signal']
            current_price = data['current_price']
            
            # 重建Signal对象
            signal = Signal.from_dict(signal_data)
            
            # 获取当前状态
            balance = self.state_store.balance
            position = None
            
            if signal.symbol in self.state_store.positions:
                position = self.state_store.positions[signal.symbol]
            
            # 执行风控检查
            result = await self.risk_manager.check_signal(
                signal, current_price, balance, position
            )
            
            await self.log(
                "INFO",
                f"风控检查: {result.decision.value} - {result.reason}"
            )
            
            if result.decision == RiskDecision.APPROVED:
                # 发送执行请求
                await self.emit(
                    MessageType.SIGNAL_APPROVED,
                    {
                        'signal': signal.to_dict(),
                        'current_price': current_price
                    },
                    target="ExecutorAgent"
                )
                
            elif result.decision == RiskDecision.MODIFIED:
                # 发送修改后的信号
                await self.emit(
                    MessageType.SIGNAL_APPROVED,
                    {
                        'signal': result.modified_signal.to_dict(),
                        'current_price': current_price,
                        'modified': True,
                        'modification_reason': result.reason
                    },
                    target="ExecutorAgent"
                )
                
            else:  # REJECTED
                await self.emit(
                    MessageType.SIGNAL_REJECTED,
                    {
                        'signal': signal.to_dict(),
                        'reason': result.reason
                    }
                )
                
        except Exception as e:
            await self.log("ERROR", f"风控检查失败: {e}")
            import traceback
            traceback.print_exc()
    
    async def _on_market_data(self, message: Message):
        """收到市场数据时检查持仓风险"""
        try:
            market_data = message.data
            symbol = market_data['symbol']
            klines = market_data['klines']
            
            if not klines:
                return
            
            current_price = klines[-1]['close']
            
            # 检查是否有持仓
            if symbol not in self.state_store.positions:
                return
            
            position = self.state_store.positions[symbol]
            
            # 检查持仓风险
            risk_alert = await self.risk_manager.check_position_risk(
                position, current_price
            )
            
            if risk_alert:
                await self.log(
                    "WARNING",
                    f"持仓风险警报: {risk_alert['reason']}"
                )
                
                if risk_alert['action'] == 'close':
                    # 发送平仓信号
                    from core.message import SignalType, Confidence
                    
                    close_signal = Signal(
                        signal_type=SignalType.CLOSE_LONG if position.side == 'long' else SignalType.CLOSE_SHORT,
                        symbol=symbol,
                        confidence=Confidence.HIGH,
                        reason=f"风控平仓: {risk_alert['reason']}",
                        amount=position.size,
                        strategy_name="RiskManager"
                    )
                    
                    await self.emit(
                        MessageType.SIGNAL_APPROVED,
                        {
                            'signal': close_signal.to_dict(),
                            'current_price': current_price,
                            'risk_close': True
                        },
                        target="ExecutorAgent"
                    )
                    
        except Exception as e:
            await self.log("ERROR", f"持仓风险检查失败: {e}")
    
    async def _position_monitor_loop(self):
        """持仓监控循环"""
        while self._running:
            try:
                await asyncio.sleep(30)  # 每30秒检查一次
                
                # 检查是否需要重置日统计
                now = datetime.now()
                if now.hour == 0 and now.minute < 1:
                    await self.state_store.reset_daily_stats()
                    await self.log("INFO", "已重置每日统计数据")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self.log("ERROR", f"监控循环错误: {e}")
    
    async def handle_message(self, message: Message):
        """处理其他消息"""
        if message.msg_type == MessageType.SYSTEM_STOP:
            await self.stop()
