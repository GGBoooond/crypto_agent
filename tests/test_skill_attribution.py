import sqlite3
import tempfile
import unittest
from pathlib import Path

from evolution.attribution import SkillAttributionJob
from harness.observability.trace import TraceRecorder


class SkillAttributionJobTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "trades.db")
        self.report_path = str(Path(self.temp_dir.name) / "skill_performance.csv")
        self.regime_path = str(Path(self.temp_dir.name) / "regime_accuracy.csv")
        self.recorder = TraceRecorder(db_path=self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_persists_multi_window_metrics(self) -> None:
        returns = [0.01, 0.02, -0.01, 0.03, -0.02]
        for index, value in enumerate(returns):
            self.recorder.record(
                {
                    "trace_id": f"trace_{index}",
                    "timestamp": f"2026-05-18T00:0{index}:00+00:00",
                    "skill_used": "skill_a",
                    "regime": "ranging",
                    "fine_regime": "range_chop",
                    "side": "BUY",
                    "forward_return_5m": value / 2,
                    "forward_return_30m": value,
                    "forward_return_4h": value * 2,
                    "holding_minutes": 5,
                    "llm_response": '{"confidence": "HIGH", "action": "EXECUTE_LONG"}',
                }
            )
        job = SkillAttributionJob(
            db_path=self.db_path,
            output_path=self.report_path,
            regime_output_path=self.regime_path,
        )
        result = job.run()
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item.sample_size, 5)
        self.assertAlmostEqual(item.profit_factor, 2.0)
        self.assertAlmostEqual(item.max_drawdown, 0.02)
        self.assertNotEqual(item.last_sample_at, "")
        with sqlite3.connect(self.db_path) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(skill_performance)")]
        self.assertIn("avg_fwd_5m", columns)
        self.assertIn("ic_pearson", columns)

    def test_profit_factor_boundaries_and_skill_formats(self) -> None:
        self.recorder.record(
            {
                "trace_id": "all_win",
                "timestamp": "2026-05-18T00:00:00+00:00",
                "skill_used": '["skill_win", "skill_json"]',
                "regime": "trend",
                "fine_regime": "breakout",
                "forward_return_30m": 0.01,
            }
        )
        self.recorder.record(
            {
                "trace_id": "all_loss",
                "timestamp": "2026-05-18T00:01:00+00:00",
                "skill_used": "skill_loss,skill_csv",
                "regime": "trend",
                "fine_regime": "breakout",
                "forward_return_30m": -0.01,
            }
        )
        result = SkillAttributionJob(db_path=self.db_path, output_path=self.report_path).run()
        by_skill = {item.skill_id: item for item in result}
        self.assertEqual(by_skill["skill_win"].profit_factor, 999.0)
        self.assertEqual(by_skill["skill_loss"].profit_factor, 0.0)
        self.assertIn("skill_json", by_skill)
        self.assertIn("skill_csv", by_skill)

    def test_confusion_matrix_counts_quadrants(self) -> None:
        rows = [
            ("tp", "BUY", 0.01),
            ("tn", "SELL", -0.01),
            ("fp", "BUY", -0.01),
            ("fn", "SELL", 0.01),
        ]
        for trace_id, side, value in rows:
            self.recorder.record(
                {
                    "trace_id": trace_id,
                    "timestamp": f"2026-05-18T00:00:0{len(trace_id)}+00:00",
                    "skill_used": "skill_a",
                    "regime": "ranging",
                    "fine_regime": "range_chop",
                    "side": side,
                    "forward_return_30m": value,
                }
            )
        job = SkillAttributionJob(
            db_path=self.db_path,
            output_path=self.report_path,
            regime_output_path=self.regime_path,
        )
        matrix = job.aggregate_fine_regime_accuracy()
        item = matrix[("ranging", "range_chop")]
        self.assertEqual(item["true_positive"], 1)
        self.assertEqual(item["true_negative"], 1)
        self.assertEqual(item["false_positive"], 1)
        self.assertEqual(item["false_negative"], 1)
        self.assertEqual(item["accuracy"], 0.5)


if __name__ == "__main__":
    unittest.main()
