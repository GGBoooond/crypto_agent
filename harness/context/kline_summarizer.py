"""Summarize raw klines into compact context."""
from typing import Any, Dict, List


class KlineSummarizer:
    """Lightweight deterministic summarizer (LLM-friendly output)."""

    def summarize(self, klines: List[Dict[str, Any]], limit: int = 30) -> Dict[str, Any]:
        if not klines:
            return {"summary": "no kline data"}

        sample = klines[-limit:]
        closes = [float(k["close"]) for k in sample]
        highs = [float(k["high"]) for k in sample]
        lows = [float(k["low"]) for k in sample]
        vols = [float(k["volume"]) for k in sample]

        first = closes[0]
        last = closes[-1]
        change_pct = ((last - first) / first * 100) if first else 0.0
        avg_vol = sum(vols) / len(vols)
        peak = max(highs)
        trough = min(lows)
        range_pct = ((peak - trough) / trough * 100) if trough else 0.0

        trend = "up" if change_pct > 1 else "down" if change_pct < -1 else "ranging"
        return {
            "trend": trend,
            "change_pct": round(change_pct, 3),
            "range_pct": round(range_pct, 3),
            "avg_volume": round(avg_vol, 3),
            "summary": (
                f"Last {len(sample)} bars: trend={trend}, change={change_pct:.2f}%, "
                f"range={range_pct:.2f}%, avg_vol={avg_vol:.2f}"
            ),
        }

