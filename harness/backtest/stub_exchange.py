"""Kline-driven exchange simulator for backtests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from exchange.base_exchange import BaseExchange, OrderResult

from .stub_state_store import BacktestPosition, BacktestStateStore, BacktestTrade


@dataclass
class MarketConstraints:
    tick_size: float = 0.0001
    min_amount: float = 1.0
    min_notional: float = 5.0


class BacktestExchange(BaseExchange):
    """Exchange simulator with pending market fill on next candle open."""

    def __init__(
        self,
        state_store: BacktestStateStore,
        constraints: Optional[MarketConstraints] = None,
        fee_taker: float = 0.0005,
        fee_maker: float = 0.0002,
    ):
        super().__init__(api_key="", secret_key="", passphrase="")
        self.state_store = state_store
        self.constraints = constraints or MarketConstraints()
        self.fee_taker = fee_taker
        self.fee_maker = fee_maker
        self._current_kline: Optional[Dict[str, Any]] = None
        self._pending_market_orders: List[Dict[str, Any]] = []
        self._last_order_id = 0

    async def initialize(self) -> bool:
        self._initialized = True
        return True

    async def fetch_balance(self) -> Dict[str, float]:
        return dict(self.state_store.balance)

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        if not self._current_kline:
            return {"symbol": symbol, "last": 0.0}
        return {
            "symbol": symbol,
            "last": float(self._current_kline["close"]),
            "bid": float(self._current_kline["close"]),
            "ask": float(self._current_kline["close"]),
            "high": float(self._current_kline["high"]),
            "low": float(self._current_kline["low"]),
            "volume": float(self._current_kline.get("volume", 0.0)),
            "timestamp": self._current_kline["timestamp"].isoformat(),
        }

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        since: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return []

    async def fetch_positions(self, symbol: str = None) -> List[Dict[str, Any]]:
        if symbol:
            position = self.state_store.positions.get(symbol)
            return [position.to_dict()] if position else []
        return [p.to_dict() for p in self.state_store.positions.values()]

    async def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reduce_only: bool = False,
    ) -> OrderResult:
        error = self._validate_amount(amount)
        if error:
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=side,
                amount=amount,
                price=0.0,
                status="failed",
                timestamp=datetime.utcnow(),
            )
        self._last_order_id += 1
        order_id = f"bt_{self._last_order_id}"
        self._pending_market_orders.append(
            {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "reduce_only": reduce_only,
            }
        )
        return OrderResult(
            order_id=order_id,
            symbol=symbol,
            side=side,
            amount=amount,
            price=0.0,
            status="open",
            filled=0.0,
            remaining=amount,
            timestamp=datetime.utcnow(),
        )

    async def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        reduce_only: bool = False,
    ) -> OrderResult:
        # MVP: degrade limit order to market semantics.
        return await self.create_market_order(
            symbol=symbol, side=side, amount=amount, reduce_only=reduce_only
        )

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        before = len(self._pending_market_orders)
        self._pending_market_orders = [
            order for order in self._pending_market_orders if order["order_id"] != order_id
        ]
        return len(self._pending_market_orders) != before

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        return True

    async def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        for order in self._pending_market_orders:
            if order["order_id"] == order_id:
                return {"id": order_id, "status": "open"}
        return {"id": order_id, "status": "closed"}

    def step(self, kline: Dict[str, Any]) -> None:
        """Move one candle and fill market orders at candle open."""
        self._current_kline = kline
        if not self._pending_market_orders:
            return
        fill_price = self._round_price(float(kline["open"]))
        pending = list(self._pending_market_orders)
        self._pending_market_orders = []
        for order in pending:
            self._apply_fill(order, fill_price)

    def _apply_fill(self, order: Dict[str, Any], fill_price: float) -> None:
        symbol = order["symbol"]
        amount = float(order["amount"])
        side = str(order["side"]).lower()
        reduce_only = bool(order["reduce_only"])
        fee = amount * fill_price * self.fee_taker
        position = self.state_store.positions.get(symbol)
        now = datetime.utcnow()

        if reduce_only and position:
            pnl = self._close_position(position, fill_price, amount)
            trade = BacktestTrade(
                trade_id=order["order_id"],
                symbol=symbol,
                side=f"close_{position.side}",
                amount=amount,
                price=fill_price,
                pnl=pnl,
                fee=fee,
                timestamp=now,
            )
            self._record_trade_sync(trade)
            return

        if side == "buy":
            self.state_store.positions[symbol] = BacktestPosition(
                symbol=symbol,
                side="long",
                size=amount,
                entry_price=fill_price,
                timestamp=now,
            )
            return
        if side == "sell":
            self.state_store.positions[symbol] = BacktestPosition(
                symbol=symbol,
                side="short",
                size=amount,
                entry_price=fill_price,
                timestamp=now,
            )

    def _close_position(self, position: BacktestPosition, fill_price: float, amount: float) -> float:
        if position.side == "long":
            pnl = (fill_price - position.entry_price) * amount
        else:
            pnl = (position.entry_price - fill_price) * amount
        self.state_store.positions.pop(position.symbol, None)
        return pnl

    def _record_trade_sync(self, trade: BacktestTrade) -> None:
        self.state_store.trades.append(trade)
        self.state_store.daily_pnl += trade.pnl
        self.state_store.stats["total_trades"] += 1
        self.state_store.stats["total_pnl"] += trade.pnl
        if trade.pnl > 0:
            self.state_store.stats["winning_trades"] += 1
            self.state_store.consecutive_losses = 0
        elif trade.pnl < 0:
            self.state_store.stats["losing_trades"] += 1
            self.state_store.consecutive_losses += 1
        total = self.state_store.stats["total_trades"]
        self.state_store.stats["win_rate"] = (
            self.state_store.stats["winning_trades"] / total if total else 0.0
        )
        self.state_store.balance["USDT"] += trade.pnl - trade.fee

    def _validate_amount(self, amount: float) -> Optional[str]:
        if amount < self.constraints.min_amount:
            return "amount below min_amount"
        if self._current_kline:
            notional = amount * float(self._current_kline["close"])
            if notional < self.constraints.min_notional:
                return "notional below min_notional"
        return None

    def _round_price(self, price: float) -> float:
        tick = self.constraints.tick_size
        if tick <= 0:
            return price
        return round(round(price / tick) * tick, 8)
