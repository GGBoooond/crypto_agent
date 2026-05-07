"""Offline evaluation utilities for traces."""
import sqlite3
from pathlib import Path
from typing import Dict, List


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

