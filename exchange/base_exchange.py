"""交易所基类"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime


@dataclass
class OrderResult:
    """订单结果"""
    order_id: str
    symbol: str
    side: str
    amount: float
    price: float
    status: str  # 'open', 'closed', 'canceled', 'failed'
    filled: float = 0.0
    remaining: float = 0.0
    fee: float = 0.0
    timestamp: datetime = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'side': self.side,
            'amount': self.amount,
            'price': self.price,
            'status': self.status,
            'filled': self.filled,
            'remaining': self.remaining,
            'fee': self.fee,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


class BaseExchange(ABC):
    """
    交易所基类
    定义标准接口，子类实现具体交易所逻辑
    """
    
    def __init__(self, api_key: str, secret_key: str, passphrase: str = None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self._initialized = False
    
    @abstractmethod
    async def initialize(self) -> bool:
        """初始化交易所连接"""
        pass
    
    @abstractmethod
    async def fetch_balance(self) -> Dict[str, float]:
        """获取账户余额"""
        pass
    
    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """获取最新行情"""
        pass
    
    @abstractmethod
    async def fetch_ohlcv(
        self, 
        symbol: str, 
        timeframe: str, 
        limit: int = 100,
        since: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """获取K线数据"""
        pass
    
    @abstractmethod
    async def fetch_positions(self, symbol: str = None) -> List[Dict[str, Any]]:
        """获取持仓"""
        pass
    
    @abstractmethod
    async def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reduce_only: bool = False
    ) -> OrderResult:
        """创建市价单"""
        pass
    
    @abstractmethod
    async def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        reduce_only: bool = False
    ) -> OrderResult:
        """创建限价单"""
        pass
    
    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消订单"""
        pass
    
    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """设置杠杆"""
        pass
    
    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """获取订单详情"""
        pass
    
    async def close(self):
        """关闭连接"""
        pass
