"""消息和信号定义"""
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Dict


class MessageType(Enum):
    """消息类型"""
    # 系统消息
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"
    SYSTEM_ERROR = "system_error"
    
    # 市场数据
    MARKET_DATA = "market_data"
    KLINE_UPDATE = "kline_update"
    TICKER_UPDATE = "ticker_update"
    
    # 交易信号
    SIGNAL_GENERATED = "signal_generated"
    SIGNAL_APPROVED = "signal_approved"
    SIGNAL_REJECTED = "signal_rejected"
    
    # 订单相关
    ORDER_REQUEST = "order_request"
    ORDER_CREATED = "order_created"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_FAILED = "order_failed"
    
    # 持仓相关
    POSITION_UPDATE = "position_update"
    POSITION_CLOSED = "position_closed"
    
    # 风控相关
    RISK_CHECK = "risk_check"
    RISK_ALERT = "risk_alert"
    RISK_STOP = "risk_stop"
    
    # 日志
    LOG = "log"


class SignalType(Enum):
    """交易信号类型"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"


class Confidence(Enum):
    """信号置信度"""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class Signal:
    """交易信号"""
    signal_type: SignalType
    symbol: str
    confidence: Confidence
    reason: str
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    amount: Optional[float] = None
    strategy_name: str = "unknown"
    weight: float = 1.0  # 策略权重
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'signal_type': self.signal_type.value,
            'symbol': self.symbol,
            'confidence': self.confidence.value,
            'reason': self.reason,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'amount': self.amount,
            'strategy_name': self.strategy_name,
            'weight': self.weight,
            'timestamp': self.timestamp.isoformat(),
            'metadata': self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Signal':
        """从字典创建"""
        return cls(
            signal_type=SignalType(data['signal_type']),
            symbol=data['symbol'],
            confidence=Confidence(data['confidence']),
            reason=data['reason'],
            stop_loss=data.get('stop_loss'),
            take_profit=data.get('take_profit'),
            amount=data.get('amount'),
            strategy_name=data.get('strategy_name', 'unknown'),
            weight=data.get('weight', 1.0),
            timestamp=datetime.fromisoformat(data['timestamp']) if 'timestamp' in data else datetime.now(),
            metadata=data.get('metadata', {})
        )


@dataclass
class Message:
    """Agent间通信消息"""
    msg_type: MessageType
    sender: str
    data: Any = None
    target: Optional[str] = None  # None表示广播
    timestamp: datetime = field(default_factory=datetime.now)
    msg_id: str = field(default_factory=lambda: datetime.now().strftime('%Y%m%d%H%M%S%f'))
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'msg_type': self.msg_type.value,
            'sender': self.sender,
            'data': self.data if not hasattr(self.data, 'to_dict') else self.data.to_dict(),
            'target': self.target,
            'timestamp': self.timestamp.isoformat(),
            'msg_id': self.msg_id
        }
