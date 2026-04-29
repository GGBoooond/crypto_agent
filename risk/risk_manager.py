"""风险管理器"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum
from loguru import logger

from core.message import Signal, SignalType, Confidence
from core.state_store import StateStore, Position
from config import RiskConfig


class RiskDecision(Enum):
    """风控决策"""
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    decision: RiskDecision
    reason: str
    original_signal: Signal
    modified_signal: Optional[Signal] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'decision': self.decision.value,
            'reason': self.reason,
            'original_signal': self.original_signal.to_dict(),
            'modified_signal': self.modified_signal.to_dict() if self.modified_signal else None
        }


class RiskManager:
    """
    风险管理器
    负责评估交易风险、控制仓位、执行止损止盈
    """
    
    def __init__(self, config: RiskConfig = None):
        self.config = config or RiskConfig()
        self.state_store = StateStore()
        
        # 历史记录
        self.check_history: List[RiskCheckResult] = []
    
    async def check_signal(
        self,
        signal: Signal,
        current_price: float,
        balance: Dict[str, float],
        position: Optional[Position] = None
    ) -> RiskCheckResult:
        """
        检查交易信号是否通过风控
        """
        reasons = []
        
        # 1. 检查是否允许交易
        if not await self.state_store.check_trading_enabled():
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                reason="交易已被禁用",
                original_signal=signal
            )
        
        # 2. 检查连续亏损
        if self.state_store.consecutive_losses >= self.config.max_consecutive_losses:
            await self.state_store.disable_trading(
                f"连续亏损{self.state_store.consecutive_losses}次"
            )
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                reason=f"连续亏损达到{self.config.max_consecutive_losses}次，暂停交易",
                original_signal=signal
            )
        
        # 3. 检查日止损
        usdt_balance = balance.get('USDT', 0)
        initial_balance = self.state_store.initial_balance or usdt_balance
        daily_loss_ratio = abs(self.state_store.daily_pnl) / initial_balance if initial_balance > 0 else 0
        
        if self.state_store.daily_pnl < 0 and daily_loss_ratio >= self.config.daily_stop_loss_ratio:
            await self.state_store.disable_trading(
                f"日亏损达到{daily_loss_ratio*100:.1f}%"
            )
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                reason=f"日亏损达到{daily_loss_ratio*100:.1f}%，超过限制{self.config.daily_stop_loss_ratio*100:.1f}%",
                original_signal=signal
            )
        
        # 4. 检查信号置信度
        if signal.confidence == Confidence.LOW and signal.signal_type != SignalType.HOLD:
            reasons.append("信号置信度较低")
            # 低置信度信号减少仓位
            if signal.amount:
                modified_signal = Signal(
                    signal_type=signal.signal_type,
                    symbol=signal.symbol,
                    confidence=signal.confidence,
                    reason=signal.reason,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    amount=signal.amount * 0.5,  # 减半
                    strategy_name=signal.strategy_name,
                    weight=signal.weight
                )
                return RiskCheckResult(
                    decision=RiskDecision.MODIFIED,
                    reason="低置信度信号，仓位减半",
                    original_signal=signal,
                    modified_signal=modified_signal
                )
        
        # 5. 检查持仓方向冲突
        if position:
            if signal.signal_type == SignalType.BUY and position.side == 'short':
                reasons.append("需要先平空仓")
            elif signal.signal_type == SignalType.SELL and position.side == 'long':
                reasons.append("需要先平多仓")
        
        # 6. 检查止损设置
        if signal.signal_type in [SignalType.BUY, SignalType.SELL]:
            if not signal.stop_loss:
                # 自动计算止损
                if signal.signal_type == SignalType.BUY:
                    auto_stop = current_price * (1 - self.config.stop_loss_ratio)
                else:
                    auto_stop = current_price * (1 + self.config.stop_loss_ratio)
                
                modified_signal = Signal(
                    signal_type=signal.signal_type,
                    symbol=signal.symbol,
                    confidence=signal.confidence,
                    reason=signal.reason,
                    stop_loss=auto_stop,
                    take_profit=signal.take_profit,
                    amount=signal.amount,
                    strategy_name=signal.strategy_name,
                    weight=signal.weight
                )
                
                return RiskCheckResult(
                    decision=RiskDecision.MODIFIED,
                    reason=f"自动设置止损价: {auto_stop:.2f}",
                    original_signal=signal,
                    modified_signal=modified_signal
                )
        
        # 7. 检查仓位大小
        if signal.amount:
            position_value = signal.amount * current_price
            max_position = usdt_balance * self.config.max_position_ratio
            
            if position_value > max_position:
                adjusted_amount = max_position / current_price
                modified_signal = Signal(
                    signal_type=signal.signal_type,
                    symbol=signal.symbol,
                    confidence=signal.confidence,
                    reason=signal.reason,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    amount=adjusted_amount,
                    strategy_name=signal.strategy_name,
                    weight=signal.weight
                )
                
                return RiskCheckResult(
                    decision=RiskDecision.MODIFIED,
                    reason=f"仓位超限，调整为{adjusted_amount:.4f}",
                    original_signal=signal,
                    modified_signal=modified_signal
                )
        
        # 通过所有检查
        result = RiskCheckResult(
            decision=RiskDecision.APPROVED,
            reason="风控通过" + (f" (注意: {', '.join(reasons)})" if reasons else ""),
            original_signal=signal
        )
        
        self.check_history.append(result)
        if len(self.check_history) > 100:
            self.check_history.pop(0)
        
        return result
    
    async def check_position_risk(
        self,
        position: Position,
        current_price: float
    ) -> Optional[Dict[str, Any]]:
        """
        检查持仓风险
        返回是否需要平仓以及原因
        """
        if not position:
            return None
        
        # 计算盈亏比例
        if position.side == 'long':
            pnl_ratio = (current_price - position.entry_price) / position.entry_price
        else:
            pnl_ratio = (position.entry_price - current_price) / position.entry_price
        
        # 检查止损
        if pnl_ratio <= -self.config.stop_loss_ratio:
            return {
                'action': 'close',
                'reason': f'触发止损，亏损{abs(pnl_ratio)*100:.2f}%',
                'pnl_ratio': pnl_ratio
            }
        
        # 检查持仓时间
        holding_hours = (datetime.now() - position.timestamp).total_seconds() / 3600
        if holding_hours > self.config.max_holding_hours:
            return {
                'action': 'close',
                'reason': f'持仓时间超过{self.config.max_holding_hours}小时',
                'holding_hours': holding_hours
            }
        
        # 移动止损检查
        if self.config.trailing_stop_enabled and pnl_ratio > self.config.trailing_stop_percent:
            # 盈利超过一定比例后，止损上移
            new_stop = position.entry_price * (1 + pnl_ratio - self.config.trailing_stop_percent)
            if position.side == 'long' and current_price < new_stop:
                return {
                    'action': 'close',
                    'reason': f'移动止损触发，锁定利润',
                    'pnl_ratio': pnl_ratio
                }
        
        return None
    
    def get_position_size(
        self,
        balance: float,
        price: float,
        leverage: int = 1
    ) -> float:
        """
        计算合适的仓位大小
        """
        max_position_value = balance * self.config.max_position_ratio
        position_size = max_position_value / price
        
        return position_size
