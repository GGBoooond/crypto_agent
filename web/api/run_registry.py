"""In-memory backtest run registry with disk report fallback."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from harness.backtest import BacktestConfig, BacktestEngine, ProgressEvent


RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_DONE = "done"
RUN_STATUS_FAILED = "failed"
ACTIVE_STATUSES = {RUN_STATUS_QUEUED, RUN_STATUS_RUNNING}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RuntimeRun:
    run_id: str
    config: Dict[str, Any]
    output_dir: str
    status: str = RUN_STATUS_QUEUED
    progress: float = 0.0
    index: int = 0
    total: int = 0
    equity: float = 0.0
    trades: int = 0
    started_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    task: Optional[asyncio.Task] = None

    def to_summary(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "progress": self.progress,
            "index": self.index,
            "total": self.total,
            "equity": self.equity,
            "trades": self.trades,
            "strategy_name": self.config.get("strategy_name", ""),
            "symbol": self.config.get("symbol", ""),
            "start": self.config.get("start", ""),
            "end": self.config.get("end", ""),
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class RunRegistry:
    """Tracks active backtest runs and exposes report queries."""

    def __init__(self, default_output_dir: str = "reports"):
        self.default_output_dir = default_output_dir
        self._runs: Dict[str, RuntimeRun] = {}
        self._listeners: Dict[str, List[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def create(self, config: BacktestConfig) -> str:
        async with self._lock:
            if any(run.status in ACTIVE_STATUSES for run in self._runs.values()):
                raise RuntimeError("a backtest run is already running")
            run_id = uuid4().hex[:12]
            runtime = RuntimeRun(
                run_id=run_id,
                config={
                    "strategy_name": config.strategy_name,
                    "symbol": config.symbol,
                    "start": config.start.isoformat(),
                    "end": config.end.isoformat(),
                    "llm_mode": config.llm_mode,
                    "initial_balance": config.initial_balance,
                    "timeframe": config.timeframe,
                },
                output_dir=config.output_dir or self.default_output_dir,
            )
            self._runs[run_id] = runtime
            runtime.task = asyncio.create_task(self._execute(runtime, config))
            return run_id

    async def _execute(self, runtime: RuntimeRun, config: BacktestConfig) -> None:
        runtime.status = RUN_STATUS_RUNNING
        runtime.updated_at = utc_now_iso()
        await self._emit(runtime.run_id, {"type": "status", "data": runtime.to_summary()})
        engine = BacktestEngine()
        try:
            async def on_progress(event: ProgressEvent) -> None:
                runtime.progress = event.progress
                runtime.index = event.index
                runtime.total = event.total
                runtime.equity = event.equity
                runtime.trades = event.trades
                runtime.updated_at = utc_now_iso()
                await self._emit(
                    runtime.run_id,
                    {
                        "type": "progress",
                        "data": {
                            "run_id": runtime.run_id,
                            "index": event.index,
                            "total": event.total,
                            "progress": event.progress,
                            "timestamp": event.timestamp,
                            "equity": event.equity,
                            "trades": event.trades,
                        },
                    },
                )

            result = await engine.run(config, on_progress=on_progress, run_id=runtime.run_id)
            runtime.status = RUN_STATUS_DONE
            runtime.progress = 1.0
            runtime.finished_at = utc_now_iso()
            runtime.updated_at = runtime.finished_at
            runtime.result = asdict(result)
            await self._emit(runtime.run_id, {"type": "done", "data": runtime.to_summary()})
        except Exception as exc:
            runtime.status = RUN_STATUS_FAILED
            runtime.error = str(exc)
            runtime.finished_at = utc_now_iso()
            runtime.updated_at = runtime.finished_at
            await self._emit(
                runtime.run_id,
                {
                    "type": "error",
                    "data": {"run_id": runtime.run_id, "message": runtime.error},
                },
            )

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._listeners.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        queues = self._listeners.get(run_id)
        if not queues:
            return
        self._listeners[run_id] = [item for item in queues if item is not queue]
        if not self._listeners[run_id]:
            self._listeners.pop(run_id, None)

    async def _emit(self, run_id: str, event: Dict[str, Any]) -> None:
        for queue in self._listeners.get(run_id, []):
            await queue.put(event)

    def list_runs(
        self,
        status: Optional[str] = None,
        strategy: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        by_run_id = {run["run_id"]: run for run in self._scan_disk_reports()}
        for runtime in self._runs.values():
            by_run_id[runtime.run_id] = runtime.to_summary()
        rows = list(by_run_id.values())
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if strategy:
            rows = [row for row in rows if row.get("strategy_name") == strategy]
        if symbol:
            rows = [row for row in rows if row.get("symbol") == symbol]
        rows.sort(key=lambda item: item.get("started_at") or "", reverse=True)
        return rows

    def get_summary(self, run_id: str) -> Optional[Dict[str, Any]]:
        runtime = self._runs.get(run_id)
        if runtime:
            return runtime.to_summary()
        for row in self._scan_disk_reports():
            if row["run_id"] == run_id:
                return row
        return None

    def get_result(self, run_id: str) -> Dict[str, Any]:
        runtime = self._runs.get(run_id)
        if runtime and runtime.result is not None:
            return runtime.result
        report_path = self._json_path(run_id)
        if not report_path.exists():
            raise FileNotFoundError(f"report not found for run_id={run_id}")
        with report_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def get_trades_csv_path(self, run_id: str) -> Path:
        path = self._csv_path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"trades csv not found for run_id={run_id}")
        return path

    def delete(self, run_id: str) -> None:
        runtime = self._runs.get(run_id)
        if runtime and runtime.status in ACTIVE_STATUSES:
            raise RuntimeError("cannot delete an active run")
        self._runs.pop(run_id, None)
        self._listeners.pop(run_id, None)
        json_path = self._json_path(run_id)
        csv_path = self._csv_path(run_id)
        if json_path.exists():
            json_path.unlink()
        if csv_path.exists():
            csv_path.unlink()

    def _scan_disk_reports(self) -> List[Dict[str, Any]]:
        output_dir = Path(self.default_output_dir)
        if not output_dir.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for json_file in output_dir.glob("backtest_*.json"):
            run_id = json_file.stem.replace("backtest_", "")
            if run_id.endswith("_trades"):
                continue
            try:
                with json_file.open("r", encoding="utf-8") as handle:
                    report = json.load(handle)
            except (OSError, ValueError):
                continue
            config = report.get("config", {})
            rows.append(
                {
                    "run_id": report.get("run_id", run_id),
                    "status": RUN_STATUS_DONE,
                    "progress": 1.0,
                    "index": 0,
                    "total": 0,
                    "equity": report.get("equity_curve", [{}])[-1].get("equity", 0.0)
                    if report.get("equity_curve")
                    else 0.0,
                    "trades": report.get("total_trades", 0),
                    "strategy_name": config.get("strategy_name", ""),
                    "symbol": config.get("symbol", ""),
                    "start": config.get("start", ""),
                    "end": config.get("end", ""),
                    "started_at": "",
                    "updated_at": "",
                    "finished_at": "",
                    "error": None,
                    "win_rate": report.get("win_rate"),
                    "sharpe": report.get("sharpe"),
                    "max_drawdown": report.get("max_drawdown"),
                }
            )
        return rows

    def _json_path(self, run_id: str) -> Path:
        return Path(self.default_output_dir) / f"backtest_{run_id}.json"

    def _csv_path(self, run_id: str) -> Path:
        return Path(self.default_output_dir) / f"backtest_{run_id}_trades.csv"


run_registry = RunRegistry()
