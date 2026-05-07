"""Market regime detection."""
from enum import Enum
from typing import Any, Dict, List


class MarketRegime(str, Enum):
    STRONG_TREND_UP = "strong_trend_up"
    WEAK_TREND_UP = "weak_trend_up"
    RANGING = "ranging"
    WEAK_TREND_DOWN = "weak_trend_down"
    STRONG_TREND_DOWN = "strong_trend_down"
    HIGH_VOLATILITY = "high_volatility"


class RegimeTagger:
    """Simple regime detector using price slope and volatility."""

    def detect(self, klines: List[Dict[str, Any]]) -> MarketRegime:
        if len(klines) < 20:
            return MarketRegime.RANGING

        closes = [float(k["close"]) for k in klines[-20:]]
        first, last = closes[0], closes[-1]
        change_pct = ((last - first) / first * 100) if first else 0.0

        returns = []
        for idx in range(1, len(closes)):
            prev = closes[idx - 1]
            curr = closes[idx]
            returns.append(abs((curr - prev) / prev * 100) if prev else 0.0)
        volatility = sum(returns) / len(returns) if returns else 0.0

        if volatility > 2.0:
            return MarketRegime.HIGH_VOLATILITY
        if change_pct >= 4:
            return MarketRegime.STRONG_TREND_UP
        if change_pct >= 1:
            return MarketRegime.WEAK_TREND_UP
        if change_pct <= -4:
            return MarketRegime.STRONG_TREND_DOWN
        if change_pct <= -1:
            return MarketRegime.WEAK_TREND_DOWN
        return MarketRegime.RANGING

