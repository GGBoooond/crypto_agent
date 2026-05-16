"""Isolated state store for backtesting runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class BacktestPosition:
    symbol: str
    side: str
    size: float
    entry_price: float
    leverage: int = 1
    unrealized_pnl: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "leverage": self.leverage,
            "unrealized_pnl": self.unrealized_pnl,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class BacktestTrade:
    trade_id: str
    symbol: str
    side: str
    amount: float
    price: float
    pnl: float = 0.0
    fee: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "amount": self.amount,
            "price": self.price,
            "pnl": self.pnl,
            "fee": self.fee,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class BacktestStateStore:
    """StateStore-compatible minimal surface for backtest."""

    def __init__(self, initial_balance: float = 10000.0):
        self.balance: Dict[str, float] = {"USDT": initial_balance}
        self.initial_balance: float = initial_balance
        self.positions: Dict[str, BacktestPosition] = {}
        self.trades: List[BacktestTrade] = []
        self.signals: List[Dict[str, Any]] = []
        self.daily_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.trading_enabled: bool = True
        self.stats: Dict[str, Any] = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
        }

    async def update_position(
        self, position: Optional[BacktestPosition], symbol: Optional[str] = None
    ) -> None:
        if position is not None:
            self.positions[position.symbol] = position
            return
        if symbol:
            self.positions.pop(symbol, None)

    async def add_trade(self, trade: BacktestTrade) -> None:
        self.trades.append(trade)
        self.daily_pnl += trade.pnl
        self.stats["total_trades"] += 1
        self.stats["total_pnl"] += trade.pnl
        if trade.pnl > 0:
            self.stats["winning_trades"] += 1
            self.consecutive_losses = 0
        elif trade.pnl < 0:
            self.stats["losing_trades"] += 1
            self.consecutive_losses += 1
        total = self.stats["total_trades"]
        self.stats["win_rate"] = self.stats["winning_trades"] / total if total else 0.0
        self.balance["USDT"] += trade.pnl - trade.fee

    async def add_signal(self, signal: Dict[str, Any]) -> None:
        self.signals.append(signal)

    async def check_trading_enabled(self) -> bool:
        return self.trading_enabled

    async def disable_trading(self, reason: str) -> None:
        self.trading_enabled = False

    async def reset_daily_stats(self) -> None:
        self.daily_pnl = 0.0
