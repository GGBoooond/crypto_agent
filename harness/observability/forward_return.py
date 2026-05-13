"""Background task that backfills trace forward returns."""
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from exchange import OKXExchange
from .trace import TraceRecorder


class ForwardReturnBackfiller:
    """Backfill 5m/30m/4h forward returns for order traces."""

    WINDOWS: Sequence[Tuple[str, int]] = (
        ("5m", 5 * 60),
        ("30m", 30 * 60),
        ("4h", 4 * 60 * 60),
    )

    def __init__(
        self,
        exchange: Optional[OKXExchange] = None,
        symbols: Sequence[str] = ("BTC/USDT:USDT", "ETH/USDT:USDT"),
        recorder: Optional[TraceRecorder] = None,
        poll_interval_seconds: int = 60,
    ):
        self.exchange = exchange or OKXExchange()
        self.symbols = tuple(symbols)
        self.recorder = recorder or TraceRecorder()
        self.poll_interval_seconds = poll_interval_seconds
        self._initialized_exchange = False

    async def run_forever(self) -> None:
        """Run periodic scans forever."""
        while True:
            try:
                await self._ensure_exchange()
                for label, seconds in self.WINDOWS:
                    await self._backfill_window(label, seconds)
            except Exception as exc:
                logger.error(f"ForwardReturnBackfiller loop failed: {exc}")
            await asyncio.sleep(self.poll_interval_seconds)

    async def _ensure_exchange(self) -> None:
        if self._initialized_exchange:
            return
        initialized = await self.exchange.initialize()
        self._initialized_exchange = initialized
        if not initialized:
            raise RuntimeError("forward return backfiller exchange init failed")

    async def _backfill_window(self, label: str, seconds: int) -> None:
        """Backfill one forward return window."""
        ready_before = datetime.utcnow() - timedelta(seconds=seconds)
        pending = self.recorder.query_pending_forward_returns(label, ready_before)
        if not pending:
            return

        for row in pending:
            trace_id = row.get("trace_id")
            symbol = row.get("symbol")
            side = str(row.get("side", "")).lower()
            entry_price = row.get("entry_price")
            entry_ts = self._parse_iso_datetime(row.get("timestamp"))
            if not trace_id or not symbol or not entry_ts or entry_price in (None, 0):
                continue
            direction_sign = 1 if side in ("buy", "long") else -1
            target_ts = entry_ts + timedelta(seconds=seconds)

            forward_return = await self._compute_symbol_forward_return(
                symbol=symbol,
                entry_price=float(entry_price),
                target_ts=target_ts,
                direction_sign=direction_sign,
            )
            if forward_return is None:
                continue

            concurrent = await self._compute_concurrent_returns(entry_ts, target_ts)
            payload = {
                f"forward_return_{label}": forward_return,
                "btc_return_concurrent": concurrent.get("btc_return_concurrent"),
                "eth_return_concurrent": concurrent.get("eth_return_concurrent"),
            }
            self.recorder.update_forward_returns(trace_id, payload)

    async def _compute_symbol_forward_return(
        self,
        symbol: str,
        entry_price: float,
        target_ts: datetime,
        direction_sign: int,
    ) -> Optional[float]:
        limit = 400
        since_ms = int((target_ts - timedelta(minutes=2)).timestamp() * 1000)
        candles = await self.exchange.fetch_ohlcv(symbol, "1m", limit=limit, since=since_ms)
        close_price = self._closest_close(candles, target_ts)
        if close_price is None or entry_price <= 0:
            return None
        return ((close_price - entry_price) / entry_price) * direction_sign

    async def _compute_concurrent_returns(
        self, entry_ts: datetime, target_ts: datetime
    ) -> Dict[str, Optional[float]]:
        returns = {"btc_return_concurrent": None, "eth_return_concurrent": None}
        mapping = {
            "BTC/USDT:USDT": "btc_return_concurrent",
            "ETH/USDT:USDT": "eth_return_concurrent",
        }
        for symbol in self.symbols:
            key = mapping.get(symbol)
            if key is None:
                continue
            since_ms = int((entry_ts - timedelta(minutes=1)).timestamp() * 1000)
            candles = await self.exchange.fetch_ohlcv(symbol, "1m", limit=600, since=since_ms)
            entry_close = self._closest_close(candles, entry_ts)
            target_close = self._closest_close(candles, target_ts)
            if entry_close is None or target_close is None or entry_close <= 0:
                returns[key] = None
            else:
                returns[key] = (target_close - entry_close) / entry_close
        return returns

    @staticmethod
    def _closest_close(candles: List[Dict[str, Any]], target_ts: datetime) -> Optional[float]:
        if not candles:
            return None
        best_row: Optional[Dict[str, Any]] = None
        best_delta = None
        for row in candles:
            ts = row.get("timestamp")
            if not isinstance(ts, datetime):
                continue
            delta = abs((ts - target_ts).total_seconds())
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_row = row
        if best_row is None:
            return None
        close_value = best_row.get("close")
        try:
            return float(close_value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_iso_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        text = str(value).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).replace(tzinfo=None)
        except ValueError:
            return None
