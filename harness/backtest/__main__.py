"""CLI entrypoint for harness backtest."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from .engine import BacktestConfig, BacktestEngine
from .fidelity import FidelityChecker


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crypto Agent backtest CLI")
    parser.add_argument("--strategy", required=True, help="strategy name")
    parser.add_argument("--start", required=True, help="start date, e.g. 2026-01-01")
    parser.add_argument("--end", required=True, help="end date, e.g. 2026-04-30")
    parser.add_argument("--symbol", required=True, help="trading symbol")
    parser.add_argument("--llm-mode", default="replay", choices=["replay", "rerun"])
    parser.add_argument("--initial-balance", type=float, default=10000.0)
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--fidelity-check", action="store_true")
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    config = BacktestConfig(
        strategy_name=args.strategy,
        start=datetime.fromisoformat(args.start),
        end=datetime.fromisoformat(args.end),
        symbol=args.symbol,
        llm_mode=args.llm_mode,
        initial_balance=args.initial_balance,
        timeframe=args.timeframe,
        output_dir=args.output_dir,
        fidelity_check=args.fidelity_check,
    )
    result = await BacktestEngine().run(config)
    print(f"run_id={result.run_id}")
    print(f"total_trades={result.total_trades}")
    print(f"win_rate={result.win_rate:.2%}")
    print(f"profit_factor={result.profit_factor:.4f}")
    print(f"max_drawdown={result.max_drawdown:.2%}")
    if args.fidelity_check:
        deviations = FidelityChecker().compare(result, config.start, config.end)
        print("fidelity_deviation=", deviations)
        if FidelityChecker().failed(deviations):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
