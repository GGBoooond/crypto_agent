"""Historical data loader with sqlite cache for backtests."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from exchange import OKXExchange


class HistoricalDataProvider:
    """Load OHLCV history and persist to local sqlite cache."""

    def __init__(self, exchange: OKXExchange, cache_dir: str = "memory/backtest_cache"):
        self.exchange = exchange
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.cache_dir / "market_cache.db"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_klines (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    PRIMARY KEY (symbol, timeframe, ts)
                )
                """
            )

    async def fetch_klines(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, Any]]:
        cached = self._load_cached_klines(symbol, timeframe, start, end)
        if cached:
            return cached
        await self._sync_klines(symbol, timeframe, start, end)
        return self._load_cached_klines(symbol, timeframe, start, end)

    async def fetch_funding_history(
        self, symbol: str, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        # MVP: funding history not required by current engine logic.
        return []

    async def _sync_klines(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> None:
        since_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        cursor_ms = since_ms
        while cursor_ms < end_ms:
            chunk = await self.exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                limit=300,
                since=cursor_ms,
            )
            if not chunk:
                break
            self._store_klines(symbol, timeframe, chunk)
            last_ts = int(chunk[-1]["timestamp"].timestamp() * 1000)
            next_cursor = last_ts + int(timedelta(minutes=1).total_seconds() * 1000)
            if next_cursor <= cursor_ms:
                break
            cursor_ms = next_cursor
            if len(chunk) < 300:
                break

    def _store_klines(self, symbol: str, timeframe: str, klines: List[Dict[str, Any]]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO backtest_klines
                (symbol, timeframe, ts, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        symbol,
                        timeframe,
                        int(k["timestamp"].timestamp() * 1000),
                        float(k["open"]),
                        float(k["high"]),
                        float(k["low"]),
                        float(k["close"]),
                        float(k.get("volume", 0.0)),
                    )
                    for k in klines
                ],
            )

    def _load_cached_klines(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT ts, open, high, low, close, volume
                FROM backtest_klines
                WHERE symbol = ? AND timeframe = ? AND ts BETWEEN ? AND ?
                ORDER BY ts ASC
                """,
                (
                    symbol,
                    timeframe,
                    int(start.timestamp() * 1000),
                    int(end.timestamp() * 1000),
                ),
            ).fetchall()
        return [
            {
                "timestamp": datetime.utcfromtimestamp(row[0] / 1000),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in rows
        ]
