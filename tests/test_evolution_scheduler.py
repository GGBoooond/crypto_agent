import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from evolution.scheduler import EvolutionScheduler
from harness.observability.trace import TraceRecorder


class EvolutionSchedulerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "trades.db")
        self.recorder = TraceRecorder(db_path=self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_daily_attribution_creates_tables(self) -> None:
        self.recorder.record(
            {
                "trace_id": "trace_1",
                "timestamp": "2026-05-18T00:00:00+00:00",
                "skill_used": "skill_a",
                "regime": "ranging",
                "fine_regime": "range_chop",
                "side": "BUY",
                "forward_return_30m": 0.01,
            }
        )
        scheduler = EvolutionScheduler(db_path=self.db_path)
        await scheduler.daily_attribution()
        import sqlite3

        with sqlite3.connect(self.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("skill_performance", tables)
        self.assertIn("skill_regime_accuracy", tables)

    def test_should_run_once_per_day(self) -> None:
        scheduler = EvolutionScheduler(db_path=self.db_path)
        now = datetime(2026, 5, 18, 0, 30, tzinfo=timezone.utc)
        self.assertTrue(scheduler._should_run("daily_attribution", 0, 30, now))
        scheduler._last_run["daily_attribution"] = now.date().isoformat()
        self.assertFalse(scheduler._should_run("daily_attribution", 0, 30, now))


if __name__ == "__main__":
    unittest.main()
