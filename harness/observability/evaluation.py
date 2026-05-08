"""Offline evaluation utilities for traces."""
import sqlite3
from pathlib import Path
from typing import Any, Dict, List


class EvaluationEngine:
    """Compute lightweight metrics from trace database."""

    def __init__(self, db_path: str = "memory/trades.db"):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def summarize(self, lookback_days: int = 30) -> Dict[str, float]:
        if not self.db_path.exists():
            return {
                "trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_pnl": 0.0,
                "ic_proxy": 0.0,
            }

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT pnl FROM traces
                WHERE pnl IS NOT NULL
                ORDER BY timestamp DESC
                """
            ).fetchall()

        pnls: List[float] = [float(r["pnl"]) for r in rows]
        if not pnls:
            return {
                "trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_pnl": 0.0,
                "ic_proxy": 0.0,
            }

        wins = [x for x in pnls if x > 0]
        losses = [abs(x) for x in pnls if x < 0]
        profit_factor = (sum(wins) / sum(losses)) if losses else float("inf")
        win_rate = len(wins) / len(pnls)
        avg_pnl = sum(pnls) / len(pnls)
        # Placeholder IC proxy: normalized avg pnl sign stability
        ic_proxy = win_rate - (1 - win_rate)

        return {
            "trades": float(len(pnls)),
            "win_rate": float(win_rate),
            "profit_factor": float(profit_factor if profit_factor != float("inf") else 999.0),
            "avg_pnl": float(avg_pnl),
            "ic_proxy": float(ic_proxy),
        }

    def context_efficiency(self) -> Dict[str, float]:
        """Aggregate prompt-token / compression metrics from the trace store.

        Returns zeroes if the database is missing or no harness-mode traces
        have been recorded yet (e.g. fresh deployment).
        """
        empty = {
            "samples": 0.0,
            "avg_prompt_tokens": 0.0,
            "avg_completion_tokens": 0.0,
            "avg_kline_compression_ratio": 0.0,
            "skill_hit_rate": 0.0,
        }
        if not self.db_path.exists():
            return empty

        try:
            with self._connect() as conn:
                rows: List[Dict[str, Any]] = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT prompt_tokens, completion_tokens,
                               kline_compression_ratio, skill_used, stage
                        FROM traces
                        WHERE stage = 'signal_generated'
                        ORDER BY timestamp DESC
                        LIMIT 500
                        """
                    ).fetchall()
                ]
        except sqlite3.OperationalError:
            return empty

        if not rows:
            return empty

        prompt_tokens = [int(r["prompt_tokens"] or 0) for r in rows]
        completion_tokens = [int(r["completion_tokens"] or 0) for r in rows]
        ratios = [float(r["kline_compression_ratio"] or 0.0) for r in rows]
        skill_hits = sum(1 for r in rows if (r.get("skill_used") or "").strip())

        sample_count = len(rows)
        return {
            "samples": float(sample_count),
            "avg_prompt_tokens": float(sum(prompt_tokens) / sample_count) if sample_count else 0.0,
            "avg_completion_tokens": (
                float(sum(completion_tokens) / sample_count) if sample_count else 0.0
            ),
            "avg_kline_compression_ratio": (
                float(sum(ratios) / sample_count) if sample_count else 0.0
            ),
            "skill_hit_rate": float(skill_hits / sample_count) if sample_count else 0.0,
        }
