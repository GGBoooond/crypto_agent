"""Unified policy gate for signal and position risk checks."""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from core.message import Confidence, Signal, SignalType
from core.state_store import Position, StateStore
from config import RiskConfig

from .schema import VerificationResult


class PolicyDecision(Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


@dataclass
class PolicyResult:
    decision: PolicyDecision
    reason: str
    original_signal: Signal
    modified_signal: Optional[Signal] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "original_signal": self.original_signal.to_dict(),
            "modified_signal": self.modified_signal.to_dict() if self.modified_signal else None,
        }


class PolicyGate:
    """Hard risk policy checks applied before execution."""

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()
        self.state_store = StateStore()
        self.check_history: List[PolicyResult] = []

    async def check_signal(
        self,
        signal: Signal,
        current_price: float,
        balance: Dict[str, float],
        position: Optional[Position] = None,
    ) -> PolicyResult:
        reasons: List[str] = []

        if not await self.state_store.check_trading_enabled():
            return PolicyResult(
                decision=PolicyDecision.REJECTED,
                reason="trading disabled",
                original_signal=signal,
            )

        if self.state_store.consecutive_losses >= self.config.max_consecutive_losses:
            await self.state_store.disable_trading(
                f"consecutive losses reached {self.state_store.consecutive_losses}"
            )
            return PolicyResult(
                decision=PolicyDecision.REJECTED,
                reason=f"max consecutive losses reached: {self.config.max_consecutive_losses}",
                original_signal=signal,
            )

        usdt_balance = balance.get("USDT", 0)
        initial_balance = self.state_store.initial_balance or usdt_balance
        daily_loss_ratio = abs(self.state_store.daily_pnl) / initial_balance if initial_balance > 0 else 0
        if self.state_store.daily_pnl < 0 and daily_loss_ratio >= self.config.daily_stop_loss_ratio:
            await self.state_store.disable_trading(
                f"daily loss reached {daily_loss_ratio:.2%}"
            )
            return PolicyResult(
                decision=PolicyDecision.REJECTED,
                reason=f"daily stop loss reached: {daily_loss_ratio:.2%}",
                original_signal=signal,
            )

        if signal.confidence == Confidence.LOW and signal.signal_type != SignalType.HOLD:
            if signal.amount:
                modified_signal = Signal(
                    signal_type=signal.signal_type,
                    symbol=signal.symbol,
                    confidence=signal.confidence,
                    reason=signal.reason,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    amount=signal.amount * 0.5,
                    strategy_name=signal.strategy_name,
                    weight=signal.weight,
                    metadata=signal.metadata,
                )
                return PolicyResult(
                    decision=PolicyDecision.MODIFIED,
                    reason="low confidence, amount halved",
                    original_signal=signal,
                    modified_signal=modified_signal,
                )
            reasons.append("low confidence")

        if signal.signal_type in [SignalType.BUY, SignalType.SELL] and not signal.stop_loss:
            auto_stop = (
                current_price * (1 - self.config.stop_loss_ratio)
                if signal.signal_type == SignalType.BUY
                else current_price * (1 + self.config.stop_loss_ratio)
            )
            modified_signal = Signal(
                signal_type=signal.signal_type,
                symbol=signal.symbol,
                confidence=signal.confidence,
                reason=signal.reason,
                stop_loss=auto_stop,
                take_profit=signal.take_profit,
                amount=signal.amount,
                strategy_name=signal.strategy_name,
                weight=signal.weight,
                metadata=signal.metadata,
            )
            return PolicyResult(
                decision=PolicyDecision.MODIFIED,
                reason=f"stop_loss auto-set to {auto_stop:.6f}",
                original_signal=signal,
                modified_signal=modified_signal,
            )

        if signal.amount:
            position_value = signal.amount * current_price
            max_position = usdt_balance * self.config.max_position_ratio
            if max_position > 0 and position_value > max_position:
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
                    weight=signal.weight,
                    metadata=signal.metadata,
                )
                return PolicyResult(
                    decision=PolicyDecision.MODIFIED,
                    reason=f"position capped to {adjusted_amount:.6f}",
                    original_signal=signal,
                    modified_signal=modified_signal,
                )

        result = PolicyResult(
            decision=PolicyDecision.APPROVED,
            reason="policy passed" + (f" ({', '.join(reasons)})" if reasons else ""),
            original_signal=signal,
        )
        self.check_history.append(result)
        if len(self.check_history) > 100:
            self.check_history.pop(0)
        return result

    async def check_position_risk(
        self,
        position: Position,
        current_price: float,
    ) -> Optional[Dict[str, Any]]:
        if not position:
            return None

        if position.side == "long":
            pnl_ratio = (current_price - position.entry_price) / position.entry_price
        else:
            pnl_ratio = (position.entry_price - current_price) / position.entry_price

        if pnl_ratio <= -self.config.stop_loss_ratio:
            return {
                "action": "close",
                "reason": f"stop loss triggered: {pnl_ratio:.2%}",
                "pnl_ratio": pnl_ratio,
            }

        holding_hours = (datetime.now() - position.timestamp).total_seconds() / 3600
        if holding_hours > self.config.max_holding_hours:
            return {
                "action": "close",
                "reason": f"max holding hours exceeded: {holding_hours:.1f}",
                "holding_hours": holding_hours,
            }

        if self.config.trailing_stop_enabled and pnl_ratio > self.config.trailing_stop_percent:
            new_stop = position.entry_price * (1 + pnl_ratio - self.config.trailing_stop_percent)
            if position.side == "long" and current_price < new_stop:
                return {
                    "action": "close",
                    "reason": "trailing stop triggered",
                    "pnl_ratio": pnl_ratio,
                }
        return None

    def get_position_size(self, balance: float, price: float, leverage: int = 1) -> float:
        max_position_value = balance * self.config.max_position_ratio
        return max_position_value / price if price > 0 else 0.0


def as_verification_result(result: PolicyResult) -> VerificationResult:
    passed = result.decision in (PolicyDecision.APPROVED, PolicyDecision.MODIFIED)
    signal = result.modified_signal or result.original_signal
    return VerificationResult(
        passed=passed,
        stage="policy",
        reason=result.reason,
        signal=signal if passed else None,
        details={"decision": result.decision.value},
    )

