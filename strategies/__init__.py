"""
策略模块 - 可插拔策略系统

使用方式：
1. 在.env中配置 ENABLED_STRATEGIES=ai_scalping,technical
2. 系统自动加载对应策略

添加新策略：
1. 创建新策略文件，继承BaseStrategy
2. 在STRATEGY_REGISTRY中注册
"""
from typing import Dict, Type, List, Optional
from loguru import logger

from .base_strategy import BaseStrategy
from .ai_strategy import AIStrategy
from .technical_strategy import TechnicalStrategy
from .trend_strategy import TrendStrategy
from .ai_scalping_strategy import AIScalpingStrategy
from .ai_hybrid_strategy import AIHybridStrategy
from .ai_hybrid_v4_strategy import AIHybridV4Strategy
from .ai_trend_sniper_strategy import AITrendSniperStrategy


# 策略注册表 - 新策略在这里注册
STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "ai": AIStrategy,
    "ai_scalping": AIScalpingStrategy,
    "ai_hybrid": AIHybridStrategy,
    "ai_hybrid_v4": AIHybridV4Strategy,  # V4: 止盈止损挂单版
    "ai_trend_sniper": AITrendSniperStrategy, # Trend Sniper: 趋势狙击手
    "technical": TechnicalStrategy,
    "trend": TrendStrategy,
}

# 默认权重配置
DEFAULT_WEIGHTS: Dict[str, float] = {
    "ai": 0.4,
    "ai_scalping": 1.0,
    "ai_hybrid": 1.0,
    "ai_hybrid_v4": 1.0,
    "ai_trend_sniper": 1.0,
    "technical": 0.3,
    "trend": 0.3,
}


class StrategyFactory:
    """策略工厂 - 动态创建策略实例"""
    
    @staticmethod
    def create(strategy_name: str, weight: Optional[float] = None) -> Optional[BaseStrategy]:
        """
        创建策略实例
        
        Args:
            strategy_name: 策略名称（在STRATEGY_REGISTRY中注册的key）
            weight: 策略权重，None则使用默认值
            
        Returns:
            策略实例，如果策略不存在返回None
        """
        strategy_class = STRATEGY_REGISTRY.get(strategy_name)
        
        if not strategy_class:
            logger.warning(f"未知策略: {strategy_name}, 可用策略: {list(STRATEGY_REGISTRY.keys())}")
            return None
        
        if weight is None:
            weight = DEFAULT_WEIGHTS.get(strategy_name, 1.0)
        
        try:
            strategy = strategy_class(weight=weight)
            logger.info(f"策略已创建: {strategy_name} (权重={weight})")
            return strategy
        except Exception as e:
            logger.error(f"创建策略失败 {strategy_name}: {e}")
            return None
    
    @staticmethod
    def create_multiple(strategy_names: List[str]) -> List[BaseStrategy]:
        """批量创建策略"""
        strategies = []
        for name in strategy_names:
            strategy = StrategyFactory.create(name)
            if strategy:
                strategies.append(strategy)
        return strategies
    
    @staticmethod
    def list_available() -> List[str]:
        """列出所有可用策略"""
        return list(STRATEGY_REGISTRY.keys())


__all__ = [
    'BaseStrategy',
    'AIStrategy', 
    'TechnicalStrategy', 
    'TrendStrategy',
    'AIScalpingStrategy',
    'AIHybridStrategy',
    'AIHybridV4Strategy',
    'AITrendSniperStrategy',
    'StrategyFactory',
    'STRATEGY_REGISTRY',
]
