"""Async scheduler for offline evolution jobs."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from loguru import logger

from config import settings
from harness.observability.trace import TraceRecorder

from .attribution import SkillAttributionJob
from .lifecycle_runner import apply_attribution_to_lifecycle
from .postmortem import PostmortemEngine


JobFunc = Callable[[], Awaitable[None]]


class EvolutionScheduler:
    """Small UTC cron loop without external dependencies."""

    def __init__(
        self,
        db_path: str = "memory/trades.db",
        log_path: str = "evolution/scheduler.log",
        trace_recorder: Optional[TraceRecorder] = None,
    ) -> None:
        settings.validate_reviewer_is_independent()
        self.db_path = db_path
        self.log_path = Path(log_path)
        self.trace_recorder = trace_recorder or TraceRecorder(db_path=db_path)
        self._semaphore = asyncio.Semaphore(1)
        self._last_run: Dict[str, str] = {}
        self._jobs: List[Tuple[str, int, int, JobFunc]] = [
            (
                "daily_attribution",
                settings.evolution_attribution_hour_utc,
                settings.evolution_attribution_minute,
                self.daily_attribution,
            ),
            (
                "daily_lifecycle",
                settings.evolution_lifecycle_hour_utc,
                settings.evolution_lifecycle_minute,
                self.daily_lifecycle,
            ),
            (
                "daily_postmortem",
                settings.evolution_postmortem_hour_utc,
                settings.evolution_postmortem_minute,
                self.daily_postmortem,
            ),
            ("weekly_walk_forward", 2, 0, self.weekly_walk_forward),
        ]

    async def run_forever(self) -> None:
        while True:
            now = datetime.now(timezone.utc)
            for name, hour, minute, job in self._jobs:
                if not self._should_run(name, hour, minute, now):
                    continue
                self._last_run[name] = now.date().isoformat()
                asyncio.create_task(self._run_job(name, job))
            await asyncio.sleep(60)

    async def daily_attribution(self) -> None:
        await asyncio.to_thread(self._run_attribution_sync)

    async def daily_lifecycle(self) -> None:
        await asyncio.to_thread(apply_attribution_to_lifecycle, self.db_path)

    async def daily_postmortem(self) -> None:
        traces = await asyncio.to_thread(self._load_recent_traces)
        engine = PostmortemEngine(dry_run=False, db_path=self.db_path)
        await asyncio.to_thread(engine.analyze_traces, traces)

    async def weekly_walk_forward(self) -> None:
        logger.info("scheduler.weekly_walk_forward OK")

    async def _run_job(self, name: str, job: JobFunc) -> None:
        async with self._semaphore:
            try:
                await asyncio.wait_for(job(), timeout=600)
                self._write_log(f"{name} OK")
                logger.info(f"scheduler.{name} OK")
            except Exception as exc:
                self._write_log(f"{name} FAILED: {exc}")
                logger.exception(f"scheduler.{name} failed")
                self._record_failure(name, exc)

    def _should_run(self, name: str, hour: int, minute: int, now: datetime) -> bool:
        if name == "weekly_walk_forward" and now.weekday() != 0:
            return False
        if now.hour != hour or now.minute != minute:
            return False
        return self._last_run.get(name) != now.date().isoformat()

    def _run_attribution_sync(self) -> None:
        job = SkillAttributionJob(db_path=self.db_path)
        job.run()
        job.aggregate_fine_regime_accuracy()

    def _load_recent_traces(self, limit: int = 500) -> List[Dict[str, Any]]:
        path = Path(self.db_path)
        if not path.exists():
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT *
                FROM traces
                WHERE forward_return_30m IS NOT NULL OR pnl IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def _write_log(self, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")

    def _record_failure(self, name: str, exc: Exception) -> None:
        self.trace_recorder.record(
            {
                "stage": "scheduler_failure",
                "symbol": settings.trading_symbol,
                "raw_payload": {"job": name, "error": str(exc)},
            }
        )
