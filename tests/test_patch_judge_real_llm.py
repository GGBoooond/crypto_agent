import tempfile
import unittest
from pathlib import Path

from evolution.judge import PatchJudge


class PatchJudgeTest(unittest.TestCase):
    def test_dual_scores_must_both_clear_threshold(self) -> None:
        judge = PatchJudge()
        accepted = judge.score({}, reviewer_a_score=0.9, reviewer_b_score=0.9)
        overfit = judge.score({}, reviewer_a_score=0.9, reviewer_b_score=0.5)
        rejected = judge.score({}, reviewer_a_score=0.5, reviewer_b_score=0.5)
        self.assertTrue(accepted["accepted"])
        self.assertFalse(overfit["accepted"])
        self.assertFalse(rejected["accepted"])

    def test_load_skill_stats_weighted_by_sample_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "trades.db"
            import sqlite3

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE skill_performance (
                        skill_id TEXT,
                        sample_size INTEGER,
                        profit_factor REAL,
                        ic_pearson REAL
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO skill_performance VALUES ('skill_a', 10, 1.0, 0.1)"
                )
                conn.execute(
                    "INSERT INTO skill_performance VALUES ('skill_a', 30, 2.0, 0.3)"
                )
            stats = PatchJudge().load_skill_stats("skill_a", str(db_path))
            self.assertEqual(stats["sample_size"], 40)
            self.assertAlmostEqual(stats["profit_factor"], 1.75)
            self.assertAlmostEqual(stats["ic_pearson"], 0.25)


if __name__ == "__main__":
    unittest.main()
