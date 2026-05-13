"""Backtest reporting utilities."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from statistics import mean, pstdev
from typing import Dict, List

from .stub_state_store import BacktestStateStore


class BacktestReporter:
    """Build and persist backtest reports."""

    def build(self, state_store: BacktestStateStore, result_obj) -> None:
        trades = state_store.trades
        pnls = [float(t.pnl) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        result_obj.total_trades = len(trades)
        result_obj.win_rate = (len(wins) / len(trades)) if trades else 0.0
        result_obj.profit_factor = (sum(wins) / sum(losses)) if losses else float("inf")
        result_obj.max_drawdown = self._max_drawdown(result_obj.equity_curve)
        result_obj.sharpe = self._sharpe(pnls)
        result_obj.sortino = self._sortino(pnls)
        result_obj.calmar = (
            (result_obj.equity_curve[-1]["equity"] - result_obj.equity_curve[0]["equity"])
            / result_obj.max_drawdown
            if result_obj.equity_curve and result_obj.max_drawdown > 0
            else 0.0
        )

    def save(self, output_dir: str, result_obj) -> Dict[str, str]:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        run_id = result_obj.run_id
        report_json = output_path / f"backtest_{run_id}.json"
        trades_csv = output_path / f"backtest_{run_id}_trades.csv"
        with report_json.open("w", encoding="utf-8") as f:
            json.dump(asdict(result_obj), f, ensure_ascii=True, indent=2, default=str)
        with trades_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["trade_id", "symbol", "side", "amount", "price", "pnl", "fee", "timestamp"])
            for trade in result_obj.trades:
                writer.writerow(
                    [
                        trade["trade_id"],
                        trade["symbol"],
                        trade["side"],
                        trade["amount"],
                        trade["price"],
                        trade["pnl"],
                        trade["fee"],
                        trade["timestamp"],
                    ]
                )
        return {"json": str(report_json), "csv": str(trades_csv)}

    @staticmethod
    def _max_drawdown(equity_curve: List[Dict[str, float]]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]["equity"]
        max_dd = 0.0
        for point in equity_curve:
            equity = point["equity"]
            if equity > peak:
                peak = equity
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
        return max_dd

    @staticmethod
    def _sharpe(pnls: List[float]) -> float:
        if len(pnls) < 2:
            return 0.0
        std = pstdev(pnls)
        if std == 0:
            return 0.0
        return mean(pnls) / std

    @staticmethod
    def _sortino(pnls: List[float]) -> float:
        negative = [p for p in pnls if p < 0]
        if len(negative) < 2:
            return 0.0
        downside = pstdev(negative)
        if downside == 0:
            return 0.0
        return mean(pnls) / downside
