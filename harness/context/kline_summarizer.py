"""Summarize raw klines into compact context."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    import pandas as pd  # type: ignore

    _PANDAS_AVAILABLE = True
except Exception:  # pragma: no cover - pandas not strictly required for summary
    pd = None  # type: ignore
    _PANDAS_AVAILABLE = False


class KlineSummarizer:
    """Lightweight deterministic summarizer (LLM-friendly output).

    Summarises a window of klines into a compact structured payload that the
    PromptBuilder can splice into the LLM prompt instead of feeding raw bars.
    """

    def summarize(
        self,
        klines: List[Dict[str, Any]],
        limit: int = 30,
        *,
        recent_n: int = 5,
        indicators_df: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Build the compressed kline summary.

        Args:
            klines: Raw kline dicts ordered oldest -> newest.
            limit: Window size for aggregate statistics (trend / vol).
            recent_n: How many bars to keep as a precise tape (5-10 typical).
            indicators_df: Optional pandas DataFrame with pre-computed indicators
                (rsi, atr, bb_width, ema50, etc.). When provided, the summary
                pulls latest indicator values into the payload so the strategy
                does not have to repeat them in the prompt.
        """
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
        avg_vol = (sum(vols) / len(vols)) if vols else 0.0
        peak = max(highs)
        trough = min(lows)
        range_pct = ((peak - trough) / trough * 100) if trough else 0.0

        trend = "up" if change_pct > 1 else "down" if change_pct < -1 else "ranging"

        recent_window = sample[-recent_n:] if recent_n > 0 else []
        last_n_compact = self._build_recent_tape(
            recent_window,
            avg_vol=avg_vol,
            indicators_df=indicators_df,
            recent_n=recent_n,
        )
        tape_signature = self._build_tape_signature(recent_window)
        volume_anomaly = bool(
            avg_vol > 0 and recent_window and float(recent_window[-1]["volume"]) > avg_vol * 2
        )

        indicators_snapshot = self._extract_indicator_snapshot(indicators_df)

        return {
            "trend": trend,
            "change_pct": round(change_pct, 3),
            "range_pct": round(range_pct, 3),
            "avg_volume": round(avg_vol, 3),
            "last_n_compact": last_n_compact,
            "tape_signature": tape_signature,
            "volume_anomaly": volume_anomaly,
            "indicators": indicators_snapshot,
            "summary": (
                f"Last {len(sample)} bars: trend={trend}, change={change_pct:.2f}%, "
                f"range={range_pct:.2f}%, avg_vol={avg_vol:.2f}"
            ),
        }

    def _build_recent_tape(
        self,
        recent_window: List[Dict[str, Any]],
        *,
        avg_vol: float,
        indicators_df: Optional[Any],
        recent_n: int,
    ) -> List[str]:
        """Render the most recent bars as compact strings.

        Each line looks like: ``T-3: 阳 O:0.11500 C:0.11620 H:... L:... RVol:1.3x [rsi=42.1]``
        """
        if not recent_window:
            return []

        first_close = float(recent_window[0]["close"])
        decimals = max(self._guess_decimals(first_close), 5)
        fmt = f".{decimals}f"

        rsi_series: Optional[List[float]] = None
        atr_value: Optional[float] = None
        if _PANDAS_AVAILABLE and indicators_df is not None:
            try:
                if "rsi" in indicators_df.columns:
                    rsi_series = [
                        float(v) if v == v else float("nan")
                        for v in indicators_df["rsi"].tail(recent_n).tolist()
                    ]
                if "atr" in indicators_df.columns:
                    last_atr = indicators_df["atr"].iloc[-1]
                    atr_value = float(last_atr) if last_atr == last_atr else None
            except Exception:
                rsi_series = None
                atr_value = None

        lines: List[str] = []
        total = len(recent_window)
        for offset, bar in enumerate(recent_window):
            o = float(bar["open"])
            c = float(bar["close"])
            h = float(bar["high"])
            low = float(bar["low"])
            v = float(bar["volume"])
            k_type = "阳" if c >= o else "阴"
            r_vol = (v / avg_vol) if avg_vol > 0 else 0.0
            rsi_part = ""
            if rsi_series is not None and offset < len(rsi_series):
                rsi_val = rsi_series[offset]
                if rsi_val == rsi_val:
                    rsi_part = f" rsi={rsi_val:.1f}"
            t_idx = -(total - offset)
            lines.append(
                f"T{t_idx}: {k_type} O:{o:{fmt}} C:{c:{fmt}} "
                f"H:{h:{fmt}} L:{low:{fmt}} RVol:{r_vol:.1f}x{rsi_part}"
            )
        if atr_value is not None and lines:
            lines[-1] += f" atr={atr_value:{fmt}}"
        return lines

    def _build_tape_signature(self, recent_window: List[Dict[str, Any]]) -> str:
        if not recent_window:
            return ""
        sequence: List[str] = []
        for bar in recent_window:
            o = float(bar["open"])
            c = float(bar["close"])
            h = float(bar["high"])
            low = float(bar["low"])
            body = abs(c - o)
            upper_shadow = h - max(o, c)
            lower_shadow = min(o, c) - low
            tag = "+" if c >= o else "-"
            if body > 0 and upper_shadow > body * 1.5:
                tag += "^"  # long upper shadow
            if body > 0 and lower_shadow > body * 1.5:
                tag += "v"  # long lower shadow
            sequence.append(tag)

        joined = " ".join(sequence)
        # Detect simple streaks for human-readable summary
        last_signs = [s[0] for s in sequence]
        streak = 1
        for i in range(len(last_signs) - 1, 0, -1):
            if last_signs[i] == last_signs[i - 1]:
                streak += 1
            else:
                break
        direction = "连阳" if last_signs[-1] == "+" else "连阴"
        signature = f"{streak}{direction}" if streak >= 2 else "无明显连阴连阳"
        return f"{signature} | seq=[{joined}]"

    def _extract_indicator_snapshot(self, indicators_df: Optional[Any]) -> Dict[str, float]:
        if not _PANDAS_AVAILABLE or indicators_df is None:
            return {}
        try:
            last = indicators_df.iloc[-1]
        except Exception:
            return {}
        snapshot: Dict[str, float] = {}
        for key in ("rsi", "atr", "bb_width", "ema50", "ema200", "macd", "hist"):
            if key in indicators_df.columns:
                try:
                    val = float(last[key])
                    if val == val:  # filter NaN
                        snapshot[key] = round(val, 6)
                except Exception:
                    continue
        return snapshot

    @staticmethod
    def _guess_decimals(price: float) -> int:
        text = f"{price}"
        if "." in text:
            return len(text.split(".")[1])
        return 2

    @staticmethod
    def estimated_tokens(text: str) -> int:
        """Cheap token estimator (~4 chars per token, OpenAI rule of thumb)."""
        if not text:
            return 0
        return max(1, int(len(text) / 4))
