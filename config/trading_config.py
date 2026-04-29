"""交易和风控配置"""
from dataclasses import dataclass, field
from typing import List
from .settings import settings


@dataclass
class TradingConfig:
    """交易配置"""
    symbol: str = field(default_factory=lambda: settings.trading_symbol)
    amount: float = field(default_factory=lambda: settings.trading_amount)
    leverage: int = field(default_factory=lambda: settings.trading_leverage)
    timeframe: str = field(default_factory=lambda: settings.trading_timeframe)
    test_mode: bool = field(default_factory=lambda: settings.test_mode)
    
    # 支持的时间周期
    valid_timeframes: List[str] = field(
        default_factory=lambda: ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
    )
    
    def validate(self) -> bool:
        """验证配置"""
        if self.timeframe not in self.valid_timeframes:
            raise ValueError(f"无效的时间周期: {self.timeframe}")
        if self.amount <= 0:
            raise ValueError(f"交易数量必须大于0: {self.amount}")
        if self.leverage < 1 or self.leverage > 125:
            raise ValueError(f"杠杆倍数不合法: {self.leverage}")
        return True


@dataclass
class RiskConfig:
    """风控配置"""
    max_position_ratio: float = field(
        default_factory=lambda: settings.max_position_ratio
    )
    stop_loss_ratio: float = field(
        default_factory=lambda: settings.stop_loss_ratio
    )
    daily_stop_loss_ratio: float = field(
        default_factory=lambda: settings.daily_stop_loss_ratio
    )
    max_leverage: int = field(
        default_factory=lambda: settings.max_leverage
    )
    max_consecutive_losses: int = field(
        default_factory=lambda: settings.max_consecutive_losses
    )
    
    # 动态止损配置
    trailing_stop_enabled: bool = True
    trailing_stop_percent: float = 0.02  # 2%移动止损
    
    # 时间止损
    max_holding_hours: int = 24  # 最长持仓时间
    
    def validate(self) -> bool:
        """验证配置"""
        if not 0 < self.max_position_ratio <= 1:
            raise ValueError("最大持仓比例必须在0-1之间")
        if not 0 < self.stop_loss_ratio <= 0.1:
            raise ValueError("止损比例必须在0-10%之间")
        return True
