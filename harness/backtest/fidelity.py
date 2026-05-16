"""Backtest vs live fidelity comparison."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict


class FidelityChecker:
    """Compare backtest metrics against live traces in same window."""

    def __init__(self, db_path: str = "memory/trades.db", threshold: float = 0.15):
        self.db_path = Path(db_path)
        self.threshold = threshold

    def compare(
        self,
        backtest_result,
        live_window_start: datetime,
        live_window_end: datetime,
    ) -> Dict[str, float]:
        live_metrics = self._live_metrics(live_window_start, live_window_end)
        bt_metrics = {
            "win_rate": float(backtest_result.win_rate or 0.0),
            "profit_factor": float(backtest_result.profit_factor or 0.0),
            "sharpe": float(backtest_result.sharpe or 0.0),
        }
        deviation: Dict[str, float] = {}
        for key, live_value in live_metrics.items():
            bt_value = bt_metrics.get(key, 0.0)
            base = abs(live_value) if abs(live_value) > 1e-8 else 1.0
            deviation[key] = abs(bt_value - live_value) / base
        return deviation

    def failed(self, deviations: Dict[str, float]) -> bool:
        return any(value > self.threshold for value in deviations.values())

    def _live_metrics(self, start: datetime, end: datetime) -> Dict[str, float]:
        if not self.db_path.exists():
            return {"win_rate": 0.0, "profit_factor": 0.0, "sharpe": 0.0}
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT pnl
                FROM traces
                WHERE stage = 'position_closed'
                  AND pnl IS NOT NULL
                  AND timestamp BETWEEN ? AND ?
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        pnls = [float(row[0]) for row in rows]
        if not pnls:
            return {"win_rate": 0.0, "profit_factor": 0.0, "sharpe": 0.0}
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / len(pnls)
        sharpe = (mean / (variance**0.5)) if variance > 0 else 0.0
        return {
            "win_rate": len(wins) / len(pnls),
            "profit_factor": (sum(wins) / sum(losses)) if losses else 0.0,
            "sharpe": sharpe,
        }
