"""日志Agent - 负责记录系统事件"""
from datetime import datetime
from typing import List, Dict, Any
from loguru import logger

from core.base_agent import BaseAgent
from core.message import Message, MessageType


class LoggerAgent(BaseAgent):
    """
    日志Agent
    负责记录所有系统事件，生成报告
    """
    
    def __init__(self):
        super().__init__(name="LoggerAgent")
        self.events: List[Dict[str, Any]] = []
        self.trade_log: List[Dict[str, Any]] = []
    
    def _register_handlers(self):
        """注册所有消息类型的处理器"""
        # 记录所有类型的消息
        for msg_type in MessageType:
            self.register_handler(msg_type, self._log_event)
    
    async def _log_event(self, message: Message):
        """记录事件"""
        event = {
            'type': message.msg_type.value,
            'sender': message.sender,
            'timestamp': message.timestamp.isoformat(),
            'data_summary': self._summarize_data(message.data)
        }
        
        self.events.append(event)
        
        # 保持事件列表在合理范围
        if len(self.events) > 1000:
            self.events = self.events[-500:]
        
        # 特殊处理某些事件类型
        if message.msg_type == MessageType.ORDER_FILLED:
            await self._log_trade(message.data)
        elif message.msg_type == MessageType.POSITION_CLOSED:
            await self._log_close(message.data)
        elif message.msg_type == MessageType.SIGNAL_GENERATED:
            await self._log_signal(message.data)
        elif message.msg_type == MessageType.RISK_ALERT:
            await self._log_risk_alert(message.data)
    
    def _summarize_data(self, data: Any) -> str:
        """生成数据摘要"""
        if data is None:
            return "无数据"
        
        if isinstance(data, dict):
            keys = list(data.keys())[:3]
            return f"包含: {', '.join(keys)}..."
        
        return str(data)[:100]
    
    async def _log_trade(self, data: Dict[str, Any]):
        """记录交易"""
        order = data.get('order', {})
        signal = data.get('signal', {})
        
        trade_record = {
            'timestamp': datetime.now().isoformat(),
            'order_id': order.get('order_id'),
            'symbol': order.get('symbol'),
            'side': order.get('side'),
            'amount': order.get('filled'),
            'price': order.get('price'),
            'signal_type': signal.get('signal_type'),
            'confidence': signal.get('confidence'),
            'strategy': signal.get('strategy_name')
        }
        
        self.trade_log.append(trade_record)
        
        await self.log(
            "INFO",
            f"交易记录: {order.get('side')} {order.get('filled')} @ {order.get('price')}"
        )
    
    async def _log_close(self, data: Dict[str, Any]):
        """记录平仓"""
        pnl = data.get('pnl', 0)
        reason = data.get('reason', '')
        
        level = "INFO" if pnl >= 0 else "WARNING"
        await self.log(
            level,
            f"平仓: 盈亏 ${pnl:+,.2f} | 原因: {reason}"
        )
    
    async def _log_signal(self, data: Dict[str, Any]):
        """记录信号"""
        signal = data.get('signal', {})
        await self.log(
            "DEBUG",
            f"信号生成: {signal.get('signal_type')} "
            f"置信度: {signal.get('confidence')}"
        )
    
    async def _log_risk_alert(self, data: Dict[str, Any]):
        """记录风控警报"""
        await self.log(
            "WARNING",
            f"风控警报: {data}"
        )
    
    async def generate_daily_report(self) -> Dict[str, Any]:
        """生成每日报告"""
        stats = self.state_store.stats
        
        report = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'total_trades': stats['total_trades'],
            'winning_trades': stats['winning_trades'],
            'losing_trades': stats['losing_trades'],
            'win_rate': f"{stats['win_rate']*100:.1f}%",
            'total_pnl': f"${stats['total_pnl']:+,.2f}",
            'daily_pnl': f"${self.state_store.daily_pnl:+,.2f}",
            'trade_log': self.trade_log[-20:]  # 最近20笔交易
        }
        
        return report
    
    async def handle_message(self, message: Message):
        """处理未注册的消息"""
        pass
