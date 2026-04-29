"""策略基类"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from core.message import Signal


class BaseStrategy(ABC):
    """
    策略基类
    所有策略都需要继承此类并实现analyze方法
    """
    
    def __init__(self, name: str, weight: float = 1.0):
        """
        初始化策略
        
        Args:
            name: 策略名称
            weight: 策略权重，用于信号融合
        """
        self.name = name
        self.weight = weight
        self.enabled = True
    
    @abstractmethod
    async def analyze(
        self,
        symbol: str,
        klines: List[Dict[str, Any]],
        market_data: Dict[str, Any],
        position: Optional[Dict[str, Any]] = None
    ) -> Optional[Signal]:
        """
        分析市场数据并生成交易信号
        
        Args:
            symbol: 交易对
            klines: K线数据
            market_data: 市场数据（行情、深度等）
            position: 当前持仓
            
        Returns:
            Signal: 交易信号，如果无信号返回None
        """
        pass
    
    def enable(self):
        """启用策略"""
        self.enabled = True
    
    def disable(self):
        """禁用策略"""
        self.enabled = False
    
    def set_weight(self, weight: float):
        """设置权重"""
        self.weight = max(0.0, min(weight, 1.0))
