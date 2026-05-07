"""Fallback strategy selector when budget is exceeded."""
from typing import Optional

from core.message import Signal
from strategies.technical_strategy import TechnicalStrategy


class StrategyDegrader:
    """Use a cheap deterministic strategy as fallback."""

    def __init__(self):
        self.technical = TechnicalStrategy(weight=1.0)

    async def fallback_signal(
        self,
        symbol: str,
        klines,
        ticker,
        position=None,
    ) -> Optional[Signal]:
        return await self.technical.analyze(symbol, klines, ticker, position)

