"""Market regime detection."""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Tuple


class MarketRegime(str, Enum):
    STRONG_TREND_UP = "strong_trend_up"
    WEAK_TREND_UP = "weak_trend_up"
    RANGING = "ranging"
    WEAK_TREND_DOWN = "weak_trend_down"
    STRONG_TREND_DOWN = "strong_trend_down"
    HIGH_VOLATILITY = "high_volatility"


@dataclass
class RegimeMetrics:
    """Numeric inputs that drove the regime decision (for prompt explanation)."""

    change_pct: float
    volatility: float
    sample_size: int


class RegimeTagger:
    """Simple regime detector using price slope and volatility."""

    def detect(self, klines: List[Dict[str, Any]]) -> MarketRegime:
        regime, _ = self.detect_with_metrics(klines)
        return regime

    def detect_with_metrics(
        self, klines: List[Dict[str, Any]]
    ) -> Tuple[MarketRegime, RegimeMetrics]:
        if len(klines) < 20:
            return MarketRegime.RANGING, RegimeMetrics(0.0, 0.0, len(klines))

        closes = [float(k["close"]) for k in klines[-20:]]
        first, last = closes[0], closes[-1]
        change_pct = ((last - first) / first * 100) if first else 0.0

        returns: List[float] = []
        for idx in range(1, len(closes)):
            prev = closes[idx - 1]
            curr = closes[idx]
            returns.append(abs((curr - prev) / prev * 100) if prev else 0.0)
        volatility = sum(returns) / len(returns) if returns else 0.0

        metrics = RegimeMetrics(
            change_pct=round(change_pct, 3),
            volatility=round(volatility, 3),
            sample_size=len(closes),
        )

        if volatility > 2.0:
            return MarketRegime.HIGH_VOLATILITY, metrics
        if change_pct >= 4:
            return MarketRegime.STRONG_TREND_UP, metrics
        if change_pct >= 1:
            return MarketRegime.WEAK_TREND_UP, metrics
        if change_pct <= -4:
            return MarketRegime.STRONG_TREND_DOWN, metrics
        if change_pct <= -1:
            return MarketRegime.WEAK_TREND_DOWN, metrics
        return MarketRegime.RANGING, metrics
